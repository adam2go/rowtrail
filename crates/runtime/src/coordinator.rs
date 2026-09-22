use crate::{
    db::Db,
    model::{JobSpec, WorkerMessage},
};
use anyhow::{Result, ensure};
use rowtrail_contracts::{FRAME_LIMIT, Request, now_ms};
use serde_json::json;
use std::{
    fs::OpenOptions,
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicU64, AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::UnixListener,
    process::Command,
};

pub async fn serve(workspace: &Path) -> Result<()> {
    let workspace = rowtrail_client::workspace_path(workspace)?;
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(workspace.join("coordinator.lock"))?;
    if fs2::FileExt::try_lock_exclusive(&lock).is_err() {
        return Ok(());
    }
    for name in ["store", "staging", "spill", "logs"] {
        std::fs::create_dir_all(workspace.join(name))?;
    }
    let db = Arc::new(Db::open(&workspace)?);
    let socket = rowtrail_client::socket_path(&workspace);
    let dir = socket.parent().unwrap();
    if !dir.exists() {
        std::fs::create_dir(dir)?;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
    }
    let metadata = std::fs::symlink_metadata(dir)?;
    ensure!(
        metadata.is_dir()
            && !metadata.file_type().is_symlink()
            && metadata.permissions().mode() & 0o077 == 0
            && metadata.uid() == unsafe { libc::geteuid() },
        "insecure socket directory"
    );
    if socket.exists() {
        std::fs::remove_file(&socket)?;
    }
    let listener = UnixListener::bind(&socket)?;
    rowtrail_client::endpoint::publish(&rowtrail_client::endpoint::Endpoint {
        descriptor_version: 1,
        api_version: rowtrail_contracts::API_VERSION.into(),
        runtime_version: env!("CARGO_PKG_VERSION").into(),
        workspace: workspace.clone(),
        socket: socket.clone(),
        store_id: db.store_id.clone(),
        pid: std::process::id(),
        uid: unsafe { libc::geteuid() },
    })?;
    let active = Arc::new(AtomicUsize::new(0));
    let busy = Arc::new(AtomicUsize::new(0));
    let touched = Arc::new(AtomicU64::new(now_ms()));
    let scheduler_db = db.clone();
    let scheduler_busy = busy.clone();
    let scheduler_touched = touched.clone();
    let scheduler = tokio::spawn(async move {
        let mut worker: Option<Worker> = None;
        let mut idle_since = Instant::now();
        loop {
            match scheduler_db.queued() {
                Ok(Some(spec)) => {
                    scheduler_busy.store(1, Ordering::SeqCst);
                    run_job_with_recovery(&scheduler_db, &spec, &mut worker).await;
                    if let Err(e) = crate::storage::clean_attempt(&scheduler_db, &spec) {
                        eprintln!("cleanup error: {e:#}");
                    }
                    idle_since = Instant::now();
                    scheduler_busy.store(0, Ordering::SeqCst);
                    scheduler_touched.store(now_ms(), Ordering::SeqCst);
                }
                Ok(None) => {
                    if idle_since.elapsed() >= Duration::from_secs(60) {
                        terminate(&mut worker).await;
                    }
                    let _ = tokio::time::timeout(
                        Duration::from_secs(60),
                        scheduler_db.work_available.notified(),
                    )
                    .await;
                }
                Err(e) => {
                    eprintln!("scheduler error: {e:#}");
                    tokio::time::sleep(Duration::from_millis(100)).await;
                }
            }
        }
    });
    let mut idle = tokio::time::interval(Duration::from_secs(1));
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    loop {
        tokio::select! {
            accepted=listener.accept()=>{let (mut stream,_)=accepted?;let peer=stream.peer_cred()?;ensure!(peer.uid()==unsafe{libc::geteuid()},"unauthorized local peer");
                let db=db.clone();let active=active.clone();let touched=touched.clone();active.fetch_add(1,Ordering::SeqCst);touched.store(now_ms(),Ordering::SeqCst);
                tokio::spawn(async move{
                    while let Ok(Ok(req))=tokio::time::timeout(Duration::from_secs(60),rowtrail_client::receive::<Request>(&mut stream)).await{
                        let response=crate::api::dispatch(db.clone(),req).await;
                        if rowtrail_client::send(&mut stream,&response).await.is_err(){break}
                        touched.store(now_ms(),Ordering::SeqCst);
                    }
                    active.fetch_sub(1,Ordering::SeqCst);touched.store(now_ms(),Ordering::SeqCst);
                });
            },
            _=idle.tick()=>{if active.load(Ordering::SeqCst)==0&&busy.load(Ordering::SeqCst)==0&&now_ms().saturating_sub(touched.load(Ordering::SeqCst))>60_000{break}},
            _=sigterm.recv()=>{break},
        }
    }
    scheduler.abort();
    let _ = scheduler.await;
    drop(listener);
    let _ = std::fs::remove_file(socket);
    let _ = std::fs::remove_file(rowtrail_client::endpoint::path(&workspace));
    drop(lock);
    Ok(())
}

struct Worker {
    child: tokio::process::Child,
    input: tokio::process::ChildStdin,
    lines: tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    token: String,
    uses: u64,
}
async fn terminate(worker: &mut Option<Worker>) {
    if let Some(mut worker) = worker.take() {
        let _ = worker.child.start_kill();
        let _ = worker.child.wait().await;
    }
}
async fn run_job_with_recovery(db: &Db, spec: &JobSpec, worker: &mut Option<Worker>) {
    if let Err(e) = run_job(db, spec, worker).await {
        terminate(worker).await;
        // A worker may die after committing a part but before its
        // acknowledgement. Treat a broken control pipe like EOF;
        // the committed revision remains readable, never replayed.
        let state = if e.downcast_ref::<std::io::Error>().is_some_and(|error| {
            matches!(
                error.kind(),
                std::io::ErrorKind::BrokenPipe
                    | std::io::ErrorKind::ConnectionReset
                    | std::io::ErrorKind::UnexpectedEof
            )
        }) {
            "interrupted"
        } else {
            "failed"
        };
        let _ = db.finish(
            spec,
            state,
            Some(json!({"code":"WORKER_LOST","message":format!("{e:#}")})),
            json!({"execution_stopped":true,"worker_exit_confirmed":true}),
        );
    }
}
async fn run_job(db: &Db, spec: &JobSpec, pool: &mut Option<Worker>) -> Result<()> {
    if let Some(worker) = pool.as_mut()
        && worker.child.try_wait()?.is_some()
    {
        *pool = None;
    }
    if pool.is_none() {
        let token = rowtrail_contracts::id("worker");
        let mut child = Command::new(std::env::current_exe()?)
            .arg("worker")
            .arg("--token")
            .arg(&token)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(
                OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(db.workspace.join("logs").join("workers.log"))?,
            )
            .kill_on_drop(true)
            .spawn()?;
        let input = child.stdin.take().unwrap();
        let lines = BufReader::new(child.stdout.take().unwrap()).lines();
        *pool = Some(Worker {
            child,
            input,
            lines,
            token,
            uses: 0,
        });
    }
    let worker = pool.as_mut().unwrap();
    let pid = worker.child.id().unwrap();
    let reused = worker.uses > 0;
    worker.uses += 1;
    db.set_worker(&spec.job_id, pid, &worker.token)?;
    worker.input.write_all(&serde_json::to_vec(spec)?).await?;
    worker.input.write_all(b"\n").await?;
    worker.input.flush().await?;
    let started = Instant::now();
    let mut stop_at = None;
    let mut publish_ms = 0.0;
    let mut part_count = 0u64;
    let mut terminal = None;
    let timeout = spec
        .query
        .as_ref()
        .map(|q| q.execution.run_timeout_ms)
        .or(spec.export.as_ref().map(|q| q.execution.run_timeout_ms))
        .unwrap();
    let mut tick = tokio::time::interval(Duration::from_millis(10));
    loop {
        tokio::select! {
            line=worker.lines.next_line()=>{
                let Some(line)=line? else{break};ensure!(line.len()<=FRAME_LIMIT,"worker control frame too large");
                match serde_json::from_str::<WorkerMessage>(&line)?{
                    WorkerMessage::Schema{schema}=>db.set_schema(spec,&schema)?,
                    WorkerMessage::Part{part}=>{
                        let published_at=Instant::now();
                        let published=db.publish(spec,&part);
                        publish_ms+=published_at.elapsed().as_secs_f64()*1000.0;
                        if published.is_ok(){part_count+=1;}
                        worker.input.write_all(if published.is_ok(){b"ok\n"}else{b"cancel\n"}).await?;worker.input.flush().await?;
                        if let Err(e)=published&& db.stopping(&spec.job_id)?.is_none(){return Err(e)}
                    },
                    WorkerMessage::Completed{metrics}=>{terminal=Some(("completed".to_owned(),None,metrics));break},
                    WorkerMessage::Failed{code,message,details,metrics}=>{
                        if code=="SOURCE_CHANGED"{for source in &spec.sources{db.invalidate(&source.id)?;}}
                        let state=if code=="RESOURCE_EXHAUSTED"||code=="BUDGET_EXHAUSTED"{"budget_exhausted"}else if code=="CANCELLED"{"cancelled"}else{"failed"};
                        terminal=Some((state.to_owned(),Some(json!({"code":code,"message":message,"retryable":false,"details":details})),metrics));break
                    }
                }
            },
            _=tick.tick()=>{
                if started.elapsed().as_millis()>=timeout as u128&&db.stopping(&spec.job_id)?.is_none(){db.cancel(&spec.job_id,"budget_exhausted")?;}
                if db.stopping(&spec.job_id)?.is_some(){
                    if stop_at.is_none(){stop_at=Some(Instant::now());unsafe{libc::kill(pid as i32,libc::SIGTERM);}}
                    if stop_at.unwrap().elapsed()>=Duration::from_millis(300){worker.child.start_kill()?;break}
                }
            }
        }
    }
    let (state, error, mut metrics) = terminal.unwrap_or_else(|| {
        (
            "interrupted".into(),
            Some(json!({"code":"WORKER_LOST","message":"worker exited without a terminal report"})),
            json!({}),
        )
    });
    let reusable = state == "completed" && db.stopping(&spec.job_id)?.is_none();
    if !reusable {
        terminate(pool).await;
    }
    metrics["publication_ms"] = json!(publish_ms);
    metrics["result_parts"] = json!(part_count);
    metrics["worker_elapsed_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0);
    metrics["execution_stopped"] = json!(true);
    metrics["worker_exit_confirmed"] = json!(!reusable);
    metrics["worker_reused"] = json!(reused);
    metrics["worker_pid"] = json!(pid);
    if let Some(stop) = stop_at {
        metrics["stop_latency_ms"] = json!(stop.elapsed().as_secs_f64() * 1000.0);
    }
    db.finish(spec, &state, error, metrics)?;
    if let Some(export) = &spec.export
        && let Some(parent) = Path::new(&export.destination).parent()
    {
        for suffix in ["tmp", "meta.tmp"] {
            let _ =
                std::fs::remove_file(parent.join(format!(".rowtrail-{}.{suffix}", spec.attempt)));
        }
    }
    for root in ["staging", "spill"] {
        let _ = std::fs::remove_dir_all(db.workspace.join(root).join(&spec.attempt));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::{
        array::Int64Array,
        datatypes::{DataType, Field, Schema},
        record_batch::RecordBatch,
    };
    use sha2::{Digest, Sha256};

    #[tokio::test]
    async fn stopped_worker_eof_uses_the_durable_stop_reason() {
        for (reason, state, code) in [
            (None, "interrupted", "WORKER_LOST"),
            (Some("cancelled"), "cancelled", "CANCELLED"),
            (
                Some("budget_exhausted"),
                "budget_exhausted",
                "BUDGET_EXHAUSTED",
            ),
        ] {
            let temp = tempfile::tempdir().unwrap();
            for dir in ["store", "staging", "spill", "logs"] {
                std::fs::create_dir(temp.path().join(dir)).unwrap();
            }
            let db = Arc::new(Db::open(temp.path()).unwrap());
            let accepted = crate::api::dispatch(
                db.clone(),
                Request::new(
                    "query",
                    json!({"bindings":{},"sql":"SELECT 7","execution":{"wait_ms":0}}),
                ),
            )
            .await;
            assert!(accepted.ok);
            let spec = db.queued().unwrap().unwrap();
            if let Some(reason) = reason {
                db.cancel(&spec.job_id, reason).unwrap();
            }
            // A real child exits without a terminal frame. The committed stop
            // request must govern both state and error, regardless of whether
            // EOF or the stop-monitor tick is observed first.
            let mut child = Command::new("sh")
                .args(["-c", "IFS= read -r job; exit 0"])
                .stdin(std::process::Stdio::piped())
                .stdout(std::process::Stdio::piped())
                .kill_on_drop(true)
                .spawn()
                .unwrap();
            let input = child.stdin.take().unwrap();
            let lines = BufReader::new(child.stdout.take().unwrap()).lines();
            let mut worker = Some(Worker {
                child,
                input,
                lines,
                token: "worker_test".into(),
                uses: 0,
            });
            run_job_with_recovery(&db, &spec, &mut worker).await;
            assert!(worker.is_none());
            let job = db.job(&spec.job_id).unwrap();
            assert_eq!(job["state"], state);
            assert_eq!(job["error"]["code"], code);
            assert_eq!(job["metrics"]["worker_exit_confirmed"], true);
            // A late competing completion cannot overwrite the terminal row.
            db.finish(&spec, "completed", None, json!({})).unwrap();
            assert_eq!(db.job(&spec.job_id).unwrap(), job);
        }
    }

    #[tokio::test]
    async fn broken_ack_pipe_preserves_committed_revision_as_interrupted() {
        let temp = tempfile::tempdir().unwrap();
        for dir in ["store", "staging", "spill", "logs"] {
            std::fs::create_dir(temp.path().join(dir)).unwrap();
        }
        let db = Arc::new(Db::open(temp.path()).unwrap());
        let accepted = crate::api::dispatch(
            db.clone(),
            rowtrail_contracts::Request::new(
                "query",
                json!({"bindings":{},"sql":"SELECT 7","execution":{"wait_ms":0}}),
            ),
        )
        .await;
        assert!(accepted.ok);
        let spec = db.queued().unwrap().unwrap();
        let schema = Schema::new(vec![Field::new("n", DataType::Int64, false)]);
        let batch = RecordBatch::try_new(
            Arc::new(schema.clone()),
            vec![Arc::new(Int64Array::from(vec![7]))],
        )
        .unwrap();
        let mut writer = arrow::ipc::writer::FileWriter::try_new(Vec::new(), &schema).unwrap();
        writer.write(&batch).unwrap();
        writer.finish().unwrap();
        let bytes = writer.into_inner().unwrap();
        let messages = [
            WorkerMessage::Schema {
                schema: schema.clone(),
            },
            WorkerMessage::Part {
                part: crate::model::Part {
                    seq: 0,
                    path: temp
                        .path()
                        .join("staging")
                        .join(&spec.attempt)
                        .join("000000000000.arrow"),
                    bytes: bytes.len() as u64,
                    rows: 1,
                    checksum: hex::encode(Sha256::digest(&bytes)),
                    schema,
                    inline_data: Some(hex::encode(bytes)),
                    checkpoint: None,
                },
            },
        ];
        let frames = temp.path().join("frames");
        std::fs::write(
            &frames,
            messages
                .iter()
                .map(|v| serde_json::to_string(v).unwrap() + "\n")
                .collect::<String>(),
        )
        .unwrap();
        // A real pipe: consume the initial job, then close the ACK reader before
        // emitting a valid part. This deterministically hits the publication race.
        let mut child = Command::new("sh")
            .args([
                "-c",
                "IFS= read -r job; exec 0<&-; cat \"$1\"",
                "worker-test",
            ])
            .arg(frames)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let input = child.stdin.take().unwrap();
        let lines = BufReader::new(child.stdout.take().unwrap()).lines();
        let mut worker = Some(Worker {
            child,
            input,
            lines,
            token: "worker_test".into(),
            uses: 0,
        });
        run_job_with_recovery(&db, &spec, &mut worker).await;
        assert!(worker.is_none());
        let state = db.job(&spec.job_id).unwrap();
        assert_eq!(state["state"], "interrupted");
        assert_eq!(state["metrics"]["worker_exit_confirmed"], true);
        let read = crate::api::dispatch(
            db,
            rowtrail_contracts::Request::new(
                "read",
                json!({"result_ref":spec.result_ref,"revision":1}),
            ),
        )
        .await;
        assert!(read.ok, "{read:?}");
        assert_eq!(read.result.unwrap()["rows"], json!([["7"]]));
    }
}
