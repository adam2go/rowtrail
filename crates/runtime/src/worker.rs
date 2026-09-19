use crate::model::{JobSpec, Part, WorkerMessage};
use anyhow::{Result, bail, ensure};
use arrow::{ipc::writer::FileWriter, record_batch::RecordBatch};
use futures::StreamExt;
use rowtrail_contracts::FRAME_LIMIT;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    fs::{File, OpenOptions},
    io::Write,
    path::Path,
    sync::Arc,
    time::Duration,
};
use tokio::io::AsyncWriteExt;

pub struct HashWriter {
    pub file: File,
    pub hash: Sha256,
    pub bytes: u64,
    pub limit: u64,
}
impl Write for HashWriter {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        if self.bytes.saturating_add(b.len() as u64) > self.limit {
            return Err(std::io::Error::other(
                "RESOURCE_EXHAUSTED: result storage budget",
            ));
        }
        let n = self.file.write(b)?;
        self.hash.update(&b[..n]);
        self.bytes += n as u64;
        Ok(n)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.file.flush()
    }
}
async fn message(value: &WorkerMessage) -> Result<()> {
    let bytes = serde_json::to_vec(value)?;
    ensure!(bytes.len() <= FRAME_LIMIT, "PROTOCOL_FRAME_TOO_LARGE");
    let mut out = tokio::io::stdout();
    out.write_all(&bytes).await?;
    out.write_all(b"\n").await?;
    out.flush().await?;
    Ok(())
}
fn stage_part(spec: &JobSpec, batch: &RecordBatch, seq: u64, remaining: u64) -> Result<Part> {
    let dir = spec.workspace.join("staging").join(&spec.attempt);
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(format!("{seq:012}.arrow"));
    let output = HashWriter {
        file: OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)?,
        hash: Sha256::new(),
        bytes: 0,
        limit: remaining.min(8 * 1024 * 1024),
    };
    let mut writer = FileWriter::try_new(output, batch.schema().as_ref())?;
    writer.write(batch)?;
    writer.finish()?;
    let mut output = writer.into_inner()?;
    output.flush()?;
    output.file.sync_all()?;
    Ok(Part {
        seq,
        path,
        bytes: output.bytes,
        rows: batch.num_rows(),
        checksum: hex::encode(output.hash.finalize()),
        schema: batch.schema().as_ref().clone(),
    })
}
pub async fn run(token: &str) -> Result<()> {
    // The native watchdog remains responsive even when a CPU-heavy future does not yield.
    let parent = unsafe { libc::getppid() };
    std::thread::spawn(move || {
        loop {
            std::thread::sleep(Duration::from_millis(100));
            if unsafe { libc::getppid() } != parent {
                unsafe { libc::_exit(71) }
            }
        }
    });
    ensure!(token.starts_with("worker_"), "invalid worker identity");
    let (job_tx, mut job_rx) = tokio::sync::mpsc::channel::<JobSpec>(1);
    let (ack_tx, mut ack_rx) = tokio::sync::mpsc::channel::<String>(2);
    let (stop_tx, mut stop_rx) = tokio::sync::watch::channel(false);
    std::thread::spawn(move || {
        use std::io::BufRead;
        let input = std::io::stdin();
        for line in input.lock().lines() {
            let Ok(line) = line else { break };
            if line.len() > FRAME_LIMIT {
                break;
            }
            if line == "cancel" {
                let _ = stop_tx.send(true);
                continue;
            }
            if line == "ok" {
                if ack_tx.blocking_send(line).is_err() {
                    break;
                }
                continue;
            }
            let Ok(spec) = serde_json::from_str::<JobSpec>(&line) else {
                break;
            };
            if job_tx.blocking_send(spec).is_err() {
                break;
            }
        }
        let _ = stop_tx.send(true);
    });
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut workspace = None;
    loop {
        let spec = tokio::select! {_=term.recv()=>break,_=stop_rx.changed()=>break,spec=job_rx.recv()=>match spec{Some(spec)=>spec,None=>break}};
        if let Some(path) = &workspace {
            ensure!(path == &spec.workspace, "worker workspace cannot change")
        } else {
            workspace = Some(spec.workspace.clone())
        }
        execute_job(&spec, &mut ack_rx, &mut stop_rx, &mut term).await?;
    }
    Ok(())
}
async fn execute_job(
    spec: &JobSpec,
    ack_rx: &mut tokio::sync::mpsc::Receiver<String>,
    stop_rx: &mut tokio::sync::watch::Receiver<bool>,
    term: &mut tokio::signal::unix::Signal,
) -> Result<()> {
    let started = std::time::Instant::now();
    let counters = Arc::new(crate::store::Counters::default());
    let result:Result<serde_json::Value>=async{
        for source in &spec.sources{source.validate()?}
        if spec.export.is_some(){return export(spec, &counters).await}
        let (ctx,df)=crate::engine::plan(spec,counters.clone()).await?;
        let plan=df.create_physical_plan().await?;
        let schema=plan.schema();message(&WorkerMessage::Schema{schema:schema.as_ref().clone()}).await?;
        let mut stream=datafusion::physical_plan::execute_stream(plan.clone(),ctx.task_ctx())?;
        let limits=&spec.query.as_ref().unwrap().execution;
        let deadline=tokio::time::sleep(Duration::from_millis(limits.run_timeout_ms));tokio::pin!(deadline);
        let mut seq=0;let mut written=0;let mut rows=0;
        loop {
            let batch=tokio::select!{biased;_=stop_rx.changed()=>bail!("CANCELLED"),_=term.recv()=>bail!("CANCELLED"),_=&mut deadline=>bail!("BUDGET_EXHAUSTED"),next=stream.next()=>match next{Some(b)=>b?,None=>break}};
            let per_row=batch.get_array_memory_size().checked_div(batch.num_rows().max(1)).unwrap_or(0).max(1);
            let chunk=(4*1024*1024/per_row).clamp(1,1024);
            for offset in (0..batch.num_rows()).step_by(chunk){
                if *stop_rx.borrow(){bail!("CANCELLED")}
                let batch=batch.slice(offset,chunk.min(batch.num_rows()-offset));
                let part=stage_part(spec,&batch,seq,limits.result_bytes.saturating_sub(written))?;
                rows+=part.rows;written+=part.bytes;
                message(&WorkerMessage::Part{part}).await?;
                let ack=tokio::select!{_=stop_rx.changed()=>bail!("CANCELLED"),_=term.recv()=>bail!("CANCELLED"),_=&mut deadline=>bail!("BUDGET_EXHAUSTED"),ack=ack_rx.recv()=>ack.ok_or_else(||anyhow::anyhow!("coordinator closed"))?};
                ensure!(ack=="ok","coordinator rejected part: {ack}");seq+=1;
            }
        }
        drop(stream);
        for source in &spec.sources{source.validate()?}
        Ok(json!({"io":counters.value(),"result_write_bytes":written,"rows":rows,"elapsed_ms":started.elapsed().as_secs_f64()*1000.0,"plan":format!("{}",datafusion::physical_plan::display::DisplayableExecutionPlan::with_metrics(plan.as_ref()).indent(true))}))
    }.await;
    match result {
        Ok(metrics) => message(&WorkerMessage::Completed { metrics }).await?,
        Err(e) => {
            message(&WorkerMessage::Failed {
                code: crate::errors::code(&e, "SQL_ERROR").into(),
                message: format!("{e:#}"),
                metrics: json!({"elapsed_ms":started.elapsed().as_secs_f64()*1000.0,"io":counters.value()}),
            })
            .await?
        }
    }
    Ok(())
}

async fn export(spec: &JobSpec, counters: &crate::store::Counters) -> Result<serde_json::Value> {
    let p = spec.export.as_ref().unwrap();
    let input = spec.inputs.values().next().unwrap();
    let target = Path::new(&p.destination);
    ensure!(
        !target.exists(),
        "INVALID_ARGUMENT: destination already exists"
    );
    let parent = target.parent().unwrap_or(Path::new("."));
    ensure!(
        parent.is_dir(),
        "INVALID_ARGUMENT: destination directory does not exist"
    );
    let staging = parent.join(format!(".rowtrail-{}.tmp", spec.attempt));
    let writer = HashWriter {
        file: OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&staging)?,
        hash: Sha256::new(),
        bytes: 0,
        limit: p.execution.result_bytes,
    };
    let mut schema_metadata = input.schema.metadata().clone();
    schema_metadata.insert(
        "rowtrail.quality".into(),
        serde_json::to_string(&spec.quality)?,
    );
    schema_metadata.insert("rowtrail.result_ref".into(), p.result_ref.clone());
    let schema = Arc::new(input.schema.clone().with_metadata(schema_metadata));
    // Read each bounded immutable part once; reserve its physical bytes before I/O.
    let reader = |part: &crate::sources::SourceFile| -> Result<_> {
        use std::sync::atomic::Ordering;
        part.validate()?;
        counters
            .reserved
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| {
                n.checked_add(part.size)
                    .filter(|n| *n <= p.execution.scan_bytes)
            })
            .map_err(|_| anyhow::anyhow!("RESOURCE_EXHAUSTED: export scan byte budget"))?;
        use std::io::Read;
        ensure!(
            part.size <= 8 * 1024 * 1024,
            "RESULT_CORRUPT: oversized result part"
        );
        let mut bytes = Vec::with_capacity(part.size as usize);
        File::open(&part.path)?
            .take(part.size + 1)
            .read_to_end(&mut bytes)?;
        ensure!(
            bytes.len() as u64 == part.size,
            "RESULT_CORRUPT: result part size changed"
        );
        counters
            .result_bytes
            .fetch_add(bytes.len() as u64, Ordering::Relaxed);
        counters.requests.fetch_add(1, Ordering::Relaxed);
        part.validate()?;
        Ok(arrow::ipc::reader::FileReader::try_new(
            std::io::Cursor::new(bytes),
            None,
        )?)
    };
    let mut rows = 0;
    let outcome: Result<HashWriter> = (|| match p.format.as_str() {
        "parquet" => {
            let properties = parquet::file::properties::WriterProperties::builder()
                .set_key_value_metadata(Some(vec![parquet::file::metadata::KeyValue::new(
                    "rowtrail.quality".into(),
                    Some(serde_json::to_string(&spec.quality)?),
                )]))
                .build();
            let mut w = parquet::arrow::ArrowWriter::try_new(writer, schema, Some(properties))?;
            for part in &input.files {
                for b in reader(part)? {
                    let b = b?;
                    rows += b.num_rows();
                    w.write(&b)?;
                }
            }
            Ok(w.into_inner()?)
        }
        "arrow" => {
            let mut w = arrow::ipc::writer::StreamWriter::try_new(writer, &schema)?;
            for part in &input.files {
                for b in reader(part)? {
                    let b = b?;
                    rows += b.num_rows();
                    w.write(&b)?;
                }
            }
            w.finish()?;
            Ok(w.into_inner()?)
        }
        "csv" => {
            let mut w = arrow::csv::WriterBuilder::new()
                .with_header(true)
                .build(writer);
            w.write(&RecordBatch::new_empty(Arc::new(input.schema.clone())))?;
            for part in &input.files {
                for b in reader(part)? {
                    let b = b?;
                    rows += b.num_rows();
                    w.write(&b)?;
                }
            }
            Ok(w.into_inner())
        }
        _ => bail!("UNSUPPORTED_OPERATION: export format"),
    })();
    let mut output = match outcome {
        Ok(o) => o,
        Err(e) => {
            let _ = std::fs::remove_file(&staging);
            return Err(e);
        }
    };
    output.flush()?;
    output.file.sync_all()?;
    let sidecar = target.with_file_name(format!(
        "{}.rowtrail.json",
        target.file_name().unwrap().to_string_lossy()
    ));
    let metadata = json!({"result_ref":p.result_ref,"revision":p.revision,"quality":spec.quality,"schema":input.schema,"rows":rows,"bytes":output.bytes,"sha256":hex::encode(output.hash.finalize())});
    let meta_staging = parent.join(format!(".rowtrail-{}.meta.tmp", spec.attempt));
    let mut meta = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&meta_staging)?;
    meta.write_all(&serde_json::to_vec_pretty(&metadata)?)?;
    meta.sync_all()?;
    Ok(
        json!({"destination":target,"manifest":sidecar,"rows":rows,"bytes":output.bytes,"io":counters.value(),"staging":staging,"meta_staging":meta_staging}),
    )
}
