use crate::model::{JobSpec, Part, WorkerMessage};
use anyhow::{Result, bail, ensure};
use arrow::{ipc::writer::FileWriter, record_batch::RecordBatch};
use futures::StreamExt;
use rowtrail_contracts::FRAME_LIMIT;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    fs::{File, OpenOptions},
    io::{BufWriter, Write},
    path::Path,
    sync::Arc,
    time::Duration,
};

pub struct HashWriter<W = BufWriter<File>> {
    pub file: W,
    pub hash: Sha256,
    pub bytes: u64,
    pub limit: u64,
}
impl<W: Write> Write for HashWriter<W> {
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
    let mut bytes = serde_json::to_vec(value)?;
    ensure!(bytes.len() <= FRAME_LIMIT, "PROTOCOL_FRAME_TOO_LARGE");
    bytes.push(b'\n');
    // Finish a frame synchronously so dropping a cancelled query future cannot
    // leave a partially written JSON frame ahead of its terminal report. The
    // external supervisor can still kill a worker blocked on a full pipe.
    let mut out = std::io::stdout().lock();
    out.write_all(&bytes)?;
    out.flush()?;
    Ok(())
}
// Seal complete IPC files, not individual engine batches. The writer holds only
// IPC metadata; incoming Arrow batches are released after each write.
const PART_TARGET: u64 = 6 * 1024 * 1024;
const PART_LIMIT: u64 = 8 * 1024 * 1024;
const PART_BATCH_LIMIT: usize = 128;

// Spill once when the inline limit is crossed. Large writes never accumulate
// in this buffer, and file publication keeps the existing sync guarantees.
struct StagedFile {
    path: std::path::PathBuf,
    memory: Vec<u8>,
    disk: Option<BufWriter<File>>,
    inline: bool,
}
impl StagedFile {
    fn finish(mut self) -> Result<Option<String>> {
        if let Some(file) = &mut self.disk {
            file.flush()?;
            file.get_ref().sync_all()?;
            Ok(None)
        } else {
            Ok(Some(hex::encode(self.memory)))
        }
    }
}
impl Write for StagedFile {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if self.disk.is_none() {
            if self.inline
                && self.memory.len().saturating_add(bytes.len()) <= crate::results::INLINE_LIMIT
            {
                self.memory.extend_from_slice(bytes);
                return Ok(bytes.len());
            }
            std::fs::create_dir_all(self.path.parent().unwrap())?;
            let mut file = BufWriter::with_capacity(
                64 * 1024,
                OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&self.path)?,
            );
            file.write_all(&self.memory)?;
            self.memory = Vec::new();
            self.disk = Some(file);
        }
        self.disk.as_mut().unwrap().write(bytes)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        if let Some(file) = &mut self.disk {
            file.flush()?;
        }
        Ok(())
    }
}
enum PartWriter {
    Arrow(FileWriter<HashWriter<StagedFile>>),
    Parquet(parquet::arrow::ArrowWriter<HashWriter<StagedFile>>),
}
struct StagedPart {
    writer: PartWriter,
    estimated_bytes: u64,
    path: std::path::PathBuf,
    rows: usize,
    batches: usize,
    opened: tokio::time::Instant,
}
impl StagedPart {
    fn occupied_bytes(&self) -> u64 {
        match &self.writer {
            // IPC writes each batch eagerly; use actual encoded bytes already
            // accepted by HashWriter. The next batch's full memory size remains
            // conservative headroom, with 2 MiB left for schema/footer overhead.
            PartWriter::Arrow(w) => w.get_ref().bytes,
            PartWriter::Parquet(_) => self.estimated_bytes,
        }
    }
    fn new(
        spec: &JobSpec,
        schema: &arrow::datatypes::Schema,
        seq: u64,
        remaining: u64,
        compress: bool,
    ) -> Result<Self> {
        let dir = spec.workspace.join("staging").join(&spec.attempt);
        let extension = if spec.prepared.is_some() {
            "parquet"
        } else {
            "arrow"
        };
        let path = dir.join(format!("{seq:012}.{extension}"));
        let output = HashWriter {
            file: StagedFile {
                path: path.clone(),
                memory: Vec::new(),
                disk: None,
                inline: spec.prepared.is_none(),
            },
            hash: Sha256::new(),
            bytes: 0,
            limit: remaining.min(PART_LIMIT),
        };
        Ok(Self {
            writer: if spec.prepared.is_some() {
                let mut quality = spec.quality.clone();
                quality["final_for_request"] = json!(true);
                let properties = parquet::file::properties::WriterProperties::builder()
                    .set_compression(parquet::basic::Compression::SNAPPY)
                    .set_max_row_group_row_count(Some(16384))
                    .set_key_value_metadata(Some(vec![parquet::file::metadata::KeyValue::new(
                        "rowtrail.quality".into(),
                        Some(quality.to_string()),
                    )]))
                    .build();
                PartWriter::Parquet(parquet::arrow::ArrowWriter::try_new(
                    output,
                    Arc::new(schema.clone()),
                    Some(properties),
                )?)
            } else {
                let options = arrow::ipc::writer::IpcWriteOptions::default();
                // Small observations/checkpoints stay plain IPC. Large parts
                // use the existing codec; incompressible buffers fall back to
                // raw Arrow buffers. The checksum covers the encoded bytes.
                let options = if compress {
                    options
                        .try_with_compression(Some(arrow::ipc::CompressionType::ZSTD))?
                        .try_with_compression_level(Some(1))?
                } else {
                    options
                };
                PartWriter::Arrow(FileWriter::try_new_with_options(output, schema, options)?)
            },
            estimated_bytes: 0,
            path,
            rows: 0,
            batches: 0,
            opened: tokio::time::Instant::now(),
        })
    }
    fn write(&mut self, batch: &RecordBatch) -> Result<()> {
        crate::numeric::validate_batch(batch)?;
        match &mut self.writer {
            PartWriter::Arrow(w) => w.write(batch)?,
            PartWriter::Parquet(w) => w.write(batch)?,
        }
        self.estimated_bytes += batch.get_array_memory_size() as u64;
        self.rows += batch.num_rows();
        self.batches += 1;
        Ok(())
    }
    fn finish(self, seq: u64, schema: &arrow::datatypes::Schema) -> Result<Part> {
        let mut output = match self.writer {
            PartWriter::Arrow(mut w) => {
                w.finish()?;
                w.into_inner()?
            }
            PartWriter::Parquet(w) => w.into_inner()?,
        };
        output.flush()?;
        let inline_data = output.file.finish()?;
        Ok(Part {
            seq,
            path: self.path,
            bytes: output.bytes,
            rows: self.rows,
            checksum: hex::encode(output.hash.finalize()),
            schema: schema.clone(),
            inline_data,
            checkpoint: None,
        })
    }
}
struct Output<'a> {
    spec: &'a JobSpec,
    schema: arrow::datatypes::SchemaRef,
    staged: Option<StagedPart>,
    parts: u64,
    dictionary_batches: bool,
    written: u64,
    rows: usize,
    write_ms: f64,
    ack_ms: f64,
}
impl Output<'_> {
    async fn publish(&mut self, ack_rx: &mut tokio::sync::mpsc::Receiver<String>) -> Result<()> {
        let Some(staged) = self.staged.take() else {
            return Ok(());
        };
        let t = std::time::Instant::now();
        let part = staged.finish(self.parts, &self.schema)?;
        self.write_ms += t.elapsed().as_secs_f64() * 1000.0;
        self.rows += part.rows;
        self.written += part.bytes;
        let t = std::time::Instant::now();
        message(&WorkerMessage::Part { part }).await?;
        ensure!(
            ack_rx.recv().await.as_deref() == Some("ok"),
            "CANCELLED: coordinator rejected part"
        );
        self.ack_ms += t.elapsed().as_secs_f64() * 1000.0;
        self.parts += 1;
        Ok(())
    }
    async fn push(
        &mut self,
        batch: &RecordBatch,
        ack_rx: &mut tokio::sync::mpsc::Receiver<String>,
    ) -> Result<()> {
        if let Some(staged) = &self.staged
            && (staged
                .occupied_bytes()
                .saturating_add(batch.get_array_memory_size() as u64)
                > if self.spec.prepared.is_some() {
                    4 * 1024 * 1024
                } else {
                    PART_TARGET
                }
                || staged.batches >= PART_BATCH_LIMIT)
        {
            self.publish(ack_rx).await?;
        }
        let t = std::time::Instant::now();
        if self.staged.is_none() {
            self.staged = Some(StagedPart::new(
                self.spec,
                &self.schema,
                self.parts,
                self.spec
                    .query
                    .as_ref()
                    .unwrap()
                    .execution
                    .result_bytes
                    .saturating_sub(self.written),
                batch.get_array_memory_size() >= 64 * 1024,
            )?);
        }
        self.staged.as_mut().unwrap().write(batch)?;
        self.write_ms += t.elapsed().as_secs_f64() * 1000.0;
        // Do not delay the first useful preview to fill a large part.
        if self.dictionary_batches
            || (self.parts == 0
                && self.spec.query.as_ref().unwrap().execution.preview == "available")
        {
            self.publish(ack_rx).await?;
        }
        Ok(())
    }
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
        if spec.analysis.is_some(){
            let timeout=spec.query.as_ref().unwrap().execution.run_timeout_ms;
            return tokio::select! {biased;
                _=stop_rx.changed()=>bail!("CANCELLED"),
                _=term.recv()=>bail!("CANCELLED"),
                _=tokio::time::sleep(Duration::from_millis(timeout))=>bail!("BUDGET_EXHAUSTED"),
                result=crate::aggregate::execute(spec,counters.clone(),ack_rx)=>result,
            }
        }
        let (ctx,df)=crate::engine::plan(spec,counters.clone()).await?;
        let plan=df.create_physical_plan().await?;
        let schema=plan.schema();message(&WorkerMessage::Schema{schema:schema.as_ref().clone()}).await?;
        let mut stream=datafusion::physical_plan::execute_stream(plan.clone(),ctx.task_ctx())?;
        let limits=&spec.query.as_ref().unwrap().execution;
        let deadline=tokio::time::sleep(Duration::from_millis(limits.run_timeout_ms));tokio::pin!(deadline);
        let planned_ms=started.elapsed().as_secs_f64()*1000.0;
        let mut output=Output {spec,schema:schema.clone(),staged:None,parts:0,dictionary_batches:schema.flattened_fields().iter().any(|f|matches!(f.data_type(),arrow::datatypes::DataType::Dictionary(_, _))),written:0,rows:0,write_ms:0.0,ack_ms:0.0};
        // One outer cancellation boundary also covers part acknowledgements.
        let execute=async {
            loop {
                let flush_at=output.staged.as_ref().map(|p|p.opened+Duration::from_millis(50));
                let batch=tokio::select! {
                    next=stream.next()=>match next {Some(b)=>b?,None=>break},
                    _=tokio::time::sleep_until(flush_at.unwrap_or_else(||tokio::time::Instant::now()+Duration::from_secs(60))), if flush_at.is_some()=>{
                        output.publish(ack_rx).await?;
                        continue;
                    }
                };
                let per_row=batch.get_array_memory_size().checked_div(batch.num_rows().max(1)).unwrap_or(0).max(1);
                let chunk=(2*1024*1024/per_row).clamp(1,8192);
                for offset in (0..batch.num_rows()).step_by(chunk) {
                    let batch=batch.slice(offset,chunk.min(batch.num_rows()-offset));
                    output.push(&batch,ack_rx).await?;
                }
            }
            if spec.prepared.is_some() && output.staged.is_none() && output.parts == 0 {
                output.push(&RecordBatch::new_empty(schema.clone()), ack_rx).await?;
            }
            output.publish(ack_rx).await?;
            Ok::<_,anyhow::Error>(())
        };
        tokio::select! {biased;
            _=stop_rx.changed()=>bail!("CANCELLED"),
            _=term.recv()=>bail!("CANCELLED"),
            _=&mut deadline=>bail!("BUDGET_EXHAUSTED"),
            result=execute=>result?,
        }
        drop(stream);
        for source in &spec.sources{source.validate()?}
        Ok(json!({"io":counters.value(),"result_write_bytes":output.written,"rows":output.rows,
            "target_partitions":ctx.copied_config().target_partitions(),
            "planning_ms":planned_ms,"result_write_ms":output.write_ms,"commit_ack_ms":output.ack_ms,
            "elapsed_ms":started.elapsed().as_secs_f64()*1000.0,
            "plan":format!("{}",datafusion::physical_plan::display::DisplayableExecutionPlan::with_metrics(plan.as_ref()).indent(true))}))
    }.await;
    match result {
        Ok(metrics) => message(&WorkerMessage::Completed { metrics }).await?,
        Err(e) => {
            message(&WorkerMessage::Failed {
                code: crate::errors::code(&e, counters.failure_code().unwrap_or("SQL_ERROR")).into(),
                message: format!("{e:#}"),
                details: crate::errors::details(&e),
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
        file: BufWriter::with_capacity(
            64 * 1024,
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&staging)?,
        ),
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
        let bytes = crate::results::verified_part(
            part,
            part.checksum
                .as_deref()
                .ok_or_else(|| anyhow::anyhow!("RESULT_CORRUPT: missing checksum"))?,
        )?;
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
                    crate::numeric::validate_batch(&b)?;
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
                    crate::numeric::validate_batch(&b)?;
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
                    crate::numeric::validate_batch(&b)?;
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
    output.file.get_ref().sync_all()?;
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

pub(crate) async fn checkpoint(
    spec: &JobSpec,
    batch: &RecordBatch,
    seq: u64,
    remaining: u64,
    progress: crate::model::Checkpoint,
    ack: &mut tokio::sync::mpsc::Receiver<String>,
) -> Result<u64> {
    let schema = batch.schema();
    let mut staged = StagedPart::new(spec, &schema, seq, remaining, false)?;
    staged.write(batch)?;
    let mut part = staged.finish(seq, &schema)?;
    part.checkpoint = Some(progress);
    let size = part.bytes;
    message(&WorkerMessage::Part { part }).await?;
    ensure!(
        ack.recv().await.as_deref() == Some("ok"),
        "CANCELLED: checkpoint rejected"
    );
    Ok(size)
}
pub(crate) async fn analysis_schema(schema: arrow::datatypes::Schema) -> Result<()> {
    message(&WorkerMessage::Schema { schema }).await
}
