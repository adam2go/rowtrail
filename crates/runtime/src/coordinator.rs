use crate::{
    db::Db,
    model::{JobSpec, WorkerMessage},
};
use anyhow::{Result, ensure};
use rowtrail_contracts::{FRAME_LIMIT, Request, now_ms};
use serde_json::json;
use std::{
    fs::OpenOptions,
    os::unix::fs::PermissionsExt,
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
            && metadata.permissions().mode() & 0o077 == 0,
        "insecure socket directory"
    );
    if socket.exists() {
        std::fs::remove_file(&socket)?;
    }
    let listener = UnixListener::bind(&socket)?;
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
                    if let Err(e) = run_job(&scheduler_db, &spec, &mut worker).await {
                        terminate(&mut worker).await;
                        let _ = scheduler_db.finish(
                            &spec,
                            "failed",
                            Some(json!({"code":"WORKER_LOST","message":format!("{e:#}")})),
                            json!({"execution_stopped":true,"worker_exit_confirmed":true}),
                        );
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
                    WorkerMessage::Failed{code,message,metrics}=>{
                        if code=="SOURCE_CHANGED"{for source in &spec.sources{db.invalidate(&source.id)?;}}
                        let state=if code=="RESOURCE_EXHAUSTED"||code=="BUDGET_EXHAUSTED"{"budget_exhausted"}else if code=="CANCELLED"{"cancelled"}else{"failed"};
                        terminal=Some((state.to_owned(),Some(json!({"code":code,"message":message})),metrics));break
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
