use crate::model::{Checkpoint, JobSpec, Part};
use anyhow::{Result, ensure};
use rowtrail_contracts::{id, now_ms, terminal};
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};
use std::{
    fs::File,
    path::{Path, PathBuf},
    sync::Mutex,
};

pub struct Db {
    pub conn: Mutex<Connection>,
    pub gc: std::sync::RwLock<()>,
    pub workspace: PathBuf,
    pub store_id: String,
    // Notifications are hints only; durable SQLite state remains authoritative.
    pub changed: tokio::sync::watch::Sender<()>,
    pub work_available: tokio::sync::Notify,
}
pub fn parse(s: String) -> Result<Value> {
    Ok(serde_json::from_str(&s)?)
}
pub fn event(c: &Connection, job: &str, kind: &str, payload: Value) -> Result<()> {
    let correlation: Option<String> = c
        .query_row(
            "SELECT json_extract(spec,'$.query.notify.correlation_id') FROM jobs WHERE id=?",
            [job],
            |r| r.get(0),
        )
        .optional()?
        .flatten();
    let value = json!({"api_version":"1","event_id":id("evt"),"type":kind,"job_id":job,"correlation_id":correlation,"occurred_at_ms":now_ms(),"payload":payload});
    c.execute(
        "INSERT INTO events(event_id,job_id,kind,payload,created) VALUES(?,?,?,?,?)",
        params![
            value["event_id"].as_str(),
            job,
            kind,
            value.to_string(),
            now_ms()
        ],
    )?;
    Ok(())
}
impl Db {
    pub fn open(workspace: &Path) -> Result<Self> {
        let c = Connection::open(workspace.join("metadata.sqlite"))?;
        c.busy_timeout(std::time::Duration::from_secs(5))?;
        c.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS catalog_labels(ref TEXT PRIMARY KEY,label TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS catalog_labels_value ON catalog_labels(label,ref);
            CREATE TABLE IF NOT EXISTS objects(id TEXT PRIMARY KEY,kind TEXT NOT NULL,data TEXT NOT NULL,validity TEXT NOT NULL DEFAULT 'valid');
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,state TEXT NOT NULL,phase TEXT NOT NULL,spec TEXT NOT NULL,attempt TEXT NOT NULL,result_ref TEXT NOT NULL,scope_ref TEXT NOT NULL,error TEXT,metrics TEXT,created INTEGER NOT NULL,started INTEGER,finished INTEGER,stop_requested TEXT,worker_pid INTEGER,worker_token TEXT);
            CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id),schema TEXT,quality TEXT NOT NULL,head INTEGER NOT NULL DEFAULT 0,validity TEXT NOT NULL DEFAULT 'valid');
            CREATE TABLE IF NOT EXISTS revisions(result_id TEXT NOT NULL REFERENCES results(id),revision INTEGER NOT NULL,cutoff INTEGER NOT NULL,rows INTEGER NOT NULL,quality TEXT NOT NULL,PRIMARY KEY(result_id,revision));
            CREATE TABLE IF NOT EXISTS parts(result_id TEXT NOT NULL REFERENCES results(id),seq INTEGER NOT NULL,path TEXT NOT NULL,rows INTEGER NOT NULL,bytes INTEGER NOT NULL,checksum TEXT NOT NULL,identity TEXT NOT NULL,PRIMARY KEY(result_id,seq));
            CREATE TABLE IF NOT EXISTS inline_parts(result_id TEXT NOT NULL,seq INTEGER NOT NULL,data BLOB NOT NULL,PRIMARY KEY(result_id,seq),FOREIGN KEY(result_id,seq) REFERENCES parts(result_id,seq) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS analysis_progress(result_id TEXT PRIMARY KEY,progress TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints(result_id TEXT NOT NULL,revision INTEGER NOT NULL,part_seq INTEGER NOT NULL,PRIMARY KEY(result_id,revision));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,job_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS events_job ON events(job_id,seq);
            CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state,created,id);
            CREATE INDEX IF NOT EXISTS objects_kind ON objects(kind);
            CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY,hash TEXT NOT NULL,response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deps(child TEXT NOT NULL,parent TEXT NOT NULL,PRIMARY KEY(child,parent));
            CREATE INDEX IF NOT EXISTS deps_parent ON deps(parent,child);
            CREATE TABLE IF NOT EXISTS retention(object TEXT PRIMARY KEY,released INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS pins(object TEXT NOT NULL,owner TEXT NOT NULL,PRIMARY KEY(object,owner));")?;
        c.execute(
            "INSERT OR IGNORE INTO meta VALUES('store_id',?)",
            [id("store")],
        )?;
        c.execute(
            "INSERT OR IGNORE INTO meta VALUES('schema_version','9')",
            [],
        )?;
        let version: String = c.query_row(
            "SELECT value FROM meta WHERE key='schema_version'",
            [],
            |r| r.get(0),
        )?;
        ensure!(
            matches!(version.as_str(), "3" | "4" | "5" | "6" | "7" | "8" | "9"),
            "PROTOCOL_VERSION_MISMATCH: metadata schema"
        );
        // Upgrade atomically; older runtimes cannot interpret new persisted query options.
        if version != "9" {
            c.execute_batch("BEGIN IMMEDIATE;
                UPDATE jobs SET spec=json_set(spec,'$.analysis.fragment_unit','manifest_file','$.analysis.checkpoint_interval_ms',0)
                  WHERE json_type(spec,'$.analysis')='object' AND json_type(spec,'$.analysis.fragment_unit') IS NULL;
                UPDATE meta SET value='9' WHERE key='schema_version'; COMMIT;")?;
        }
        let store_id = c.query_row("SELECT value FROM meta WHERE key='store_id'", [], |r| {
            r.get(0)
        })?;
        let db = Self {
            conn: Mutex::new(c),
            gc: std::sync::RwLock::new(()),
            workspace: workspace.to_owned(),
            store_id,
            changed: tokio::sync::watch::channel(()).0,
            work_available: tokio::sync::Notify::new(),
        };
        db.recover()?;
        Ok(db)
    }
    fn recover(&self) -> Result<()> {
        // Never kill a bare persisted PID: confirm the unique worker token first.
        let previous = {
            let c = self.conn.lock().unwrap();

            c.prepare("SELECT worker_pid,worker_token FROM jobs WHERE state IN ('running','stopping') AND worker_pid IS NOT NULL")?.query_map([],|r|Ok((r.get::<_,u32>(0)?,r.get::<_,String>(1)?)))?.collect::<rusqlite::Result<Vec<_>>>()?
        };
        for (pid, attempt) in previous {
            let output = std::process::Command::new("ps")
                .args(["-p", &pid.to_string(), "-o", "command="])
                .output()?;
            let command = String::from_utf8_lossy(&output.stdout);
            if command.contains("rowtrail-runtime worker")
                && command.contains(&format!("--token {attempt}"))
            {
                unsafe {
                    libc::kill(pid as i32, libc::SIGKILL);
                }
                let mut stopped = false;
                for _ in 0..100 {
                    let state = std::process::Command::new("ps")
                        .args(["-p", &pid.to_string(), "-o", "stat="])
                        .output()?;
                    let state = String::from_utf8_lossy(&state.stdout);
                    if state.trim().is_empty() || state.trim().starts_with('Z') {
                        stopped = true;
                        break;
                    }
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
                ensure!(
                    stopped,
                    "WORKER_LOST: previous worker did not stop; recovery must wait"
                );
            }
        }
        let specs = self
            .conn
            .lock()
            .unwrap()
            .prepare("SELECT spec FROM jobs WHERE state IN ('running','stopping')")?
            .query_map([], |r| r.get::<_, String>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for spec in specs {
            let spec: JobSpec = serde_json::from_str(&spec)?;
            self.finish(&spec, "interrupted",
                Some(json!({"code":"WORKER_LOST","message":"coordinator restarted; execution cannot resume in place"})),
                json!({"execution_stopped":true,"worker_exit_confirmed":true,"recovered":true}))?;
            crate::storage::clean_attempt(self, &spec)?;
        }
        Ok(())
    }
    pub fn set_worker(&self, job: &str, pid: u32, token: &str) -> Result<()> {
        self.conn.lock().unwrap().execute(
            "UPDATE jobs SET worker_pid=?,worker_token=? WHERE id=?",
            params![pid, token, job],
        )?;
        Ok(())
    }
    pub fn object(&self, id: &str) -> Result<Value> {
        let c = self.conn.lock().unwrap();
        let s: Option<(String, String)> = c
            .query_row("SELECT data,validity FROM objects WHERE id=?", [id], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .optional()?;
        let (s, validity) = s.ok_or_else(|| anyhow::anyhow!("OBJECT_NOT_FOUND: {id}"))?;
        ensure!(validity != "expired", "OBJECT_EXPIRED: {id}");
        let mut value = parse(s)?;
        value["validity"] = json!(validity);
        Ok(value)
    }
    pub fn job(&self, id: &str) -> Result<Value> {
        let c = self.conn.lock().unwrap();
        Self::job_in(&c, id)
    }
    pub fn snapshot(&self, id: &str) -> Result<Value> {
        let mut c = self.conn.lock().unwrap();
        let tx = c.transaction()?;
        let job = Self::job_in(&tx, id)?;
        let seq: u64 = tx.query_row("SELECT COALESCE(MAX(seq),0) FROM events", [], |r| r.get(0))?;
        tx.commit()?;
        Ok(json!({"job":job,"event_watermark":seq.to_string(),"store_id":self.store_id}))
    }
    fn job_in(c: &Connection, id: &str) -> Result<Value> {
        let row=c.query_row("SELECT state,phase,result_ref,scope_ref,error,metrics,created,started,finished,stop_requested FROM jobs WHERE id=?",[id],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?,r.get::<_,String>(2)?,r.get::<_,String>(3)?,r.get::<_,Option<String>>(4)?,r.get::<_,Option<String>>(5)?,r.get::<_,u64>(6)?,r.get::<_,Option<u64>>(7)?,r.get::<_,Option<u64>>(8)?,r.get::<_,Option<String>>(9)?))).optional()?.ok_or_else(||anyhow::anyhow!("OBJECT_NOT_FOUND: {id}"))?;
        let head: Option<u64> = c
            .query_row("SELECT head FROM results WHERE id=?", [&row.2], |r| {
                r.get(0)
            })
            .optional()?
            .filter(|h| *h > 0);
        let mut metrics = row.5.map(parse).transpose()?;
        if let Some(m) = metrics.as_mut().and_then(Value::as_object_mut) {
            if m.remove("plan").is_some() {
                m.insert("plan_available".into(), json!(true));
            }
            m.remove("staging");
            m.remove("meta_staging");
        }
        let prepared: Option<String> = c.query_row(
            "SELECT json_extract(spec,'$.prepared') FROM jobs WHERE id=?",
            [id],
            |r| r.get(0),
        )?;
        Ok(
            json!({"prepared":prepared.map(parse).transpose()?,"id":id,"state":row.0,"phase":row.1,"result_ref":row.2,"scope_ref":row.3,"readable_revision":head,"error":row.4.map(parse).transpose()?,"metrics":metrics,"created_ms":row.6,"started_ms":row.7,"finished_ms":row.8,"stop_requested":row.9}),
        )
    }
    pub fn queued(&self) -> Result<Option<JobSpec>> {
        let mut c = self.conn.lock().unwrap();
        let tx = c.transaction()?;
        let raw: Option<String> = tx
            .query_row(
                "SELECT spec FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1",
                [],
                |r| r.get(0),
            )
            .optional()?;
        let Some(raw) = raw else { return Ok(None) };
        let spec: JobSpec = serde_json::from_str(&raw)?;
        tx.execute("UPDATE jobs SET state='running',phase='planning',started=? WHERE id=? AND state='queued'",params![now_ms(),spec.job_id])?;
        event(&tx, &spec.job_id, "job.started", json!({"state":"running"}))?;
        tx.commit()?;
        self.changed.send_replace(());
        Ok(Some(spec))
    }
    pub fn stopping(&self, job: &str) -> Result<Option<String>> {
        Ok(self.conn.lock().unwrap().query_row(
            "SELECT stop_requested FROM jobs WHERE id=?",
            [job],
            |r| r.get(0),
        )?)
    }
    pub fn cancel(&self, job: &str, reason: &str) -> Result<()> {
        let mut c = self.conn.lock().unwrap();
        let tx = c.transaction()?;
        let state: String =
            tx.query_row("SELECT state FROM jobs WHERE id=?", [job], |r| r.get(0))?;
        if terminal(&state) {
            return Ok(());
        }
        if state == "queued" {
            tx.execute(
                "UPDATE jobs SET state=?,phase=?,stop_requested=?,finished=? WHERE id=?",
                params![reason, reason, reason, now_ms(), job],
            )?;
            event(&tx, job, &format!("job.{reason}"), json!({"state":reason}))?;
        } else {
            tx.execute("UPDATE jobs SET state='stopping',stop_requested=COALESCE(stop_requested,?) WHERE id=?",params![reason,job])?;
        }
        tx.commit()?;
        self.changed.send_replace(());
        Ok(())
    }
    pub fn set_schema(&self, spec: &JobSpec, schema: &arrow::datatypes::Schema) -> Result<()> {
        let mut c = self.conn.lock().unwrap();
        let tx = c.transaction()?;
        tx.execute(
            "UPDATE results SET schema=? WHERE id=?",
            params![serde_json::to_string(schema)?, spec.result_ref],
        )?;
        tx.execute(
            "UPDATE jobs SET phase='executing' WHERE id=?",
            [&spec.job_id],
        )?;
        tx.commit()?;
        Ok(())
    }
    pub fn publish(&self, spec: &JobSpec, part: &Part) -> Result<()> {
        let mut c = self.conn.lock().unwrap();
        let tx = c.transaction()?;
        let (state, attempt): (String, String) = tx.query_row(
            "SELECT state,attempt FROM jobs WHERE id=?",
            [&spec.job_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        ensure!(
            state == "running" && attempt == spec.attempt,
            "CANCELLED: publication rejected"
        );
        let existing: Option<String> = tx
            .query_row(
                "SELECT checksum FROM parts WHERE result_id=? AND seq=?",
                params![spec.result_ref, part.seq],
                |r| r.get(0),
            )
            .optional()?;
        if let Some(hash) = existing {
            ensure!(hash == part.checksum, "RESULT_CONFLICT");
            return Ok(());
        }
        let expected: u64 = tx.query_row(
            "SELECT COUNT(*) FROM parts WHERE result_id=?",
            [&spec.result_ref],
            |r| r.get(0),
        )?;
        ensure!(part.seq == expected, "RESULT_CONFLICT: out-of-order part");
        let extension = if spec.prepared.is_some() {
            "parquet"
        } else {
            "arrow"
        };
        let staging = self
            .workspace
            .join("staging")
            .join(&spec.attempt)
            .join(format!("{:012}.{extension}", part.seq));
        ensure!(
            part.path == staging,
            "INVALID_ARGUMENT: invalid staging descriptor"
        );
        let dir = self.workspace.join("store").join(&spec.result_ref);
        let saved_schema: String = tx.query_row(
            "SELECT schema FROM results WHERE id=?",
            [&spec.result_ref],
            |r| r.get(0),
        )?;
        let saved_schema: arrow::datatypes::Schema = serde_json::from_str(&saved_schema)?;
        ensure!(
            saved_schema == part.schema,
            "RESULT_CORRUPT: part schema differs from result schema"
        );
        let dest = dir.join(format!("{:012}.{extension}", part.seq));
        let inline = part
            .inline_data
            .as_ref()
            .map(|raw| -> Result<Vec<u8>> {
                ensure!(
                    spec.prepared.is_none()
                        && part.bytes <= crate::results::INLINE_LIMIT as u64
                        && raw.len() == part.bytes as usize * 2,
                    "RESULT_CORRUPT: inline descriptor"
                );
                let bytes = hex::decode(raw)?;
                use sha2::{Digest, Sha256};
                ensure!(
                    hex::encode(Sha256::digest(&bytes)) == part.checksum,
                    "RESULT_CORRUPT: inline checksum"
                );
                Ok(bytes)
            })
            .transpose()?;
        let identity = if inline.is_some() {
            crate::sources::SourceFile {
                path: dest.clone(),
                size: part.bytes,
                device: 0,
                inode: 0,
                mtime_sec: 0,
                mtime_nsec: 0,
                rows: Some(part.rows as u64),
                row_groups: None,
                checksum: Some(part.checksum.clone()),
                inline: Some(crate::sources::InlineSource {
                    database: self.workspace.join("metadata.sqlite"),
                    result_ref: spec.result_ref.clone(),
                    seq: part.seq,
                }),
            }
        } else {
            if part.seq == 0 || !dir.exists() {
                std::fs::create_dir_all(&dir)?;
                File::open(self.workspace.join("store"))?.sync_all()?;
            }
            if staging.exists() {
                ensure!(
                    !std::fs::symlink_metadata(&staging)?
                        .file_type()
                        .is_symlink(),
                    "INVALID_ARGUMENT: symlink part"
                );
                ensure!(
                    std::fs::metadata(&staging)?.len() == part.bytes,
                    "RESULT_CORRUPT: size mismatch"
                );
                std::fs::rename(&staging, &dest)?;
                #[cfg(test)]
                crate::publication_tests::fault("after_part_rename");
                File::open(&dir)?.sync_all()?;
            } else {
                ensure!(
                    dest.exists() && std::fs::metadata(&dest)?.len() == part.bytes,
                    "RESULT_UNAVAILABLE"
                )
            }
            crate::sources::SourceFile::inspect(&dest)?
        };
        tx.execute(
            "INSERT INTO parts VALUES(?,?,?,?,?,?,?)",
            params![
                spec.result_ref,
                part.seq,
                dest.to_str(),
                part.rows,
                part.bytes,
                part.checksum,
                serde_json::to_string(&identity)?
            ],
        )?;
        if let Some(bytes) = inline {
            tx.execute(
                "INSERT INTO inline_parts VALUES(?,?,?)",
                params![spec.result_ref, part.seq, bytes],
            )?;
            #[cfg(test)]
            crate::publication_tests::fault("before_inline_commit");
        }
        ensure!(
            spec.analysis.is_some() == part.checkpoint.is_some(),
            "RESULT_CORRUPT: checkpoint descriptor"
        );
        if let Some(progress) = &part.checkpoint {
            let p = spec.analysis.as_ref().unwrap();
            let files = &spec.inputs.values().next().unwrap().files;
            ensure!(
                progress.unit == p.fragment_unit
                    && progress.total_files == files.len()
                    && progress.completed_files <= files.len()
                    && part.rows == 1,
                "RESULT_CORRUPT: checkpoint coverage"
            );
            ensure!(
                progress
                    .total_fragments
                    .is_none_or(|n| progress.completed_fragments <= n),
                "RESULT_CORRUPT: fragment coverage"
            );
            if progress.unit == "manifest_file" {
                ensure!(
                    progress.completed_fragments == progress.completed_files
                        && progress.total_fragments == Some(files.len()),
                    "RESULT_CORRUPT: file coverage"
                );
            } else if let Some(total) = files.iter().map(|f| f.row_groups).sum::<Option<usize>>() {
                ensure!(
                    progress.total_fragments == Some(total),
                    "RESULT_CORRUPT: row group total"
                );
                let complete = files
                    .iter()
                    .take(progress.completed_files)
                    .map(|f| f.row_groups.unwrap())
                    .sum::<usize>();
                let upper = complete
                    + files
                        .get(progress.completed_files)
                        .map(|f| f.row_groups.unwrap())
                        .unwrap_or(0);
                ensure!(
                    (complete..=upper).contains(&progress.completed_fragments),
                    "RESULT_CORRUPT: row group prefix"
                );
            }
            let previous: Option<String> = tx
                .query_row(
                    "SELECT progress FROM analysis_progress WHERE result_id=?",
                    [&spec.result_ref],
                    |r| r.get(0),
                )
                .optional()?;
            if let Some(raw) = previous {
                let prior: Checkpoint = serde_json::from_str(&raw)?;
                ensure!(
                    progress.completed_files >= prior.completed_files
                        && progress.completed_fragments >= prior.completed_fragments
                        && progress.processed_rows >= prior.processed_rows
                        && (progress.completed_fragments > prior.completed_fragments
                            || progress.completed_files > prior.completed_files),
                    "RESULT_CORRUPT: nonmonotonic coverage"
                );
            }
            if p.execution.preview == "none" {
                ensure!(
                    part.seq == 0
                        && progress.completed_files == files.len()
                        && progress.total_fragments == Some(progress.completed_fragments),
                    "RESULT_CORRUPT: incomplete final checkpoint"
                );
            }
            tx.execute("INSERT INTO analysis_progress VALUES(?,?) ON CONFLICT(result_id) DO UPDATE SET progress=excluded.progress",params![spec.result_ref,serde_json::to_string(progress)?])?;
        }
        let preview = spec.prepared.is_none()
            && spec
                .query
                .as_ref()
                .is_some_and(|q| q.execution.preview == "available");
        if preview {
            let rev: u64 = tx.query_row(
                "SELECT head+1 FROM results WHERE id=?",
                [&spec.result_ref],
                |r| r.get(0),
            )?;
            let rows: u64 = tx.query_row(
                "SELECT COALESCE(SUM(rows),0) FROM parts WHERE result_id=?",
                [&spec.result_ref],
                |r| r.get(0),
            )?;
            let mut quality = spec.quality.clone();
            quality["coverage"]["kind"] = json!("partial");
            quality["coverage"]["input_coverage"] = json!("unknown");
            quality["final_for_request"] = json!(false);
            let rows = if let Some(progress) = &part.checkpoint {
                quality["coverage"]["input_coverage"] = progress.coverage();
                tx.execute(
                    "INSERT INTO checkpoints VALUES(?,?,?)",
                    params![spec.result_ref, rev, part.seq],
                )?;
                1
            } else {
                rows
            };
            tx.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?)",
                params![
                    spec.result_ref,
                    rev,
                    part.seq + 1,
                    rows,
                    quality.to_string()
                ],
            )?;
            tx.execute(
                "UPDATE results SET head=? WHERE id=?",
                params![rev, spec.result_ref],
            )?;
            event(
                &tx,
                &spec.job_id,
                "result.ready",
                json!({"result_ref":spec.result_ref,"revision":rev,"quality":quality}),
            )?;
        }
        tx.commit()?;
        #[cfg(test)]
        crate::publication_tests::fault("after_part_commit");
        self.changed.send_replace(());
        Ok(())
    }
    pub fn finish(
        &self,
        spec: &JobSpec,
        state: &str,
        error: Option<Value>,
        metrics: Value,
    ) -> Result<()> {
        let mut c = self.conn.lock().unwrap();
        let tx = c.transaction()?;
        let (current, stop): (String, Option<String>) = tx.query_row(
            "SELECT state,stop_requested FROM jobs WHERE id=?",
            [&spec.job_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        if terminal(&current) {
            return Ok(());
        }
        let state = stop.as_deref().unwrap_or(state);
        // Cancellation/timeout is authoritative in this same transaction for
        // both state and error. A forced exit, broken ACK, or late terminal
        // frame must not leave a synthetic WORKER_LOST on a stopped job.
        let error = match stop.as_deref() {
            Some("cancelled") => Some(json!({"code":"CANCELLED","message":"execution cancelled"})),
            Some("budget_exhausted") => {
                Some(json!({"code":"BUDGET_EXHAUSTED","message":"execution time budget exhausted"}))
            }
            _ => error,
        };
        if state == "completed"
            && let Some(export) = &spec.export
        {
            let target = std::path::Path::new(&export.destination);
            let parent = target.parent().unwrap();
            let staging = parent.join(format!(".rowtrail-{}.tmp", spec.attempt));
            let meta_staging = parent.join(format!(".rowtrail-{}.meta.tmp", spec.attempt));
            let sidecar = target.with_file_name(format!(
                "{}.rowtrail.json",
                target.file_name().unwrap().to_string_lossy()
            ));
            ensure!(
                metrics["staging"].as_str() == staging.to_str()
                    && metrics["meta_staging"].as_str() == meta_staging.to_str(),
                "RESULT_CORRUPT: export descriptor"
            );
            std::fs::hard_link(&meta_staging, &sidecar)?;
            if let Err(e) = std::fs::hard_link(&staging, target) {
                let _ = std::fs::remove_file(&sidecar);
                return Err(e.into());
            }
            File::open(parent)?.sync_all()?;
            std::fs::remove_file(staging)?;
            std::fs::remove_file(meta_staging)?;
        }
        if state != "completed"
            && spec.query.is_some()
            && spec.prepared.is_none()
            && spec.analysis.is_none()
        {
            let head: u64 = tx.query_row(
                "SELECT head FROM results WHERE id=?",
                [&spec.result_ref],
                |r| r.get(0),
            )?;
            let (cutoff, rows): (u64, u64) = tx.query_row(
                "SELECT COUNT(*),COALESCE(SUM(rows),0) FROM parts WHERE result_id=?",
                [&spec.result_ref],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )?;
            if head == 0 && cutoff > 0 {
                let mut quality = spec.quality.clone();
                quality["coverage"]["kind"] = json!("partial");
                quality["final_for_request"] = json!(false);
                tx.execute(
                    "INSERT INTO revisions VALUES(?,1,?,?,?)",
                    params![spec.result_ref, cutoff, rows, quality.to_string()],
                )?;
                tx.execute("UPDATE results SET head=1 WHERE id=?", [&spec.result_ref])?;
                event(
                    &tx,
                    &spec.job_id,
                    "result.ready",
                    json!({"result_ref":spec.result_ref,"revision":1,"quality":quality}),
                )?;
            }
        }
        if state == "completed"
            && spec.query.is_some()
            && spec.prepared.is_none()
            && spec.analysis.is_none()
        {
            let (head, schema): (u64, Option<String>) = tx.query_row(
                "SELECT head,schema FROM results WHERE id=?",
                [&spec.result_ref],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )?;
            ensure!(
                schema.is_some(),
                "RESULT_CORRUPT: completion without schema"
            );
            let (cutoff, rows): (u64, u64) = tx.query_row(
                "SELECT COUNT(*),COALESCE(SUM(rows),0) FROM parts WHERE result_id=?",
                [&spec.result_ref],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )?;
            let mut quality = spec.quality.clone();
            quality["final_for_request"] = json!(true);
            tx.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?)",
                params![spec.result_ref, head + 1, cutoff, rows, quality.to_string()],
            )?;
            tx.execute(
                "UPDATE results SET head=? WHERE id=?",
                params![head + 1, spec.result_ref],
            )?;
            event(
                &tx,
                &spec.job_id,
                "result.ready",
                json!({"result_ref":spec.result_ref,"revision":head+1,"quality":quality}),
            )?;
        }
        if state == "completed" && spec.analysis.is_some() {
            let (head,cutoff): (u64,u64) = tx.query_row("SELECT head,(SELECT COUNT(*) FROM parts WHERE result_id=results.id) FROM results WHERE id=?",[&spec.result_ref],|r|Ok((r.get(0)?,r.get(1)?)))?;
            ensure!(
                cutoff > 0,
                "RESULT_CORRUPT: analysis completed without checkpoint"
            );
            let raw: String = tx.query_row(
                "SELECT progress FROM analysis_progress WHERE result_id=?",
                [&spec.result_ref],
                |r| r.get(0),
            )?;
            let progress: Checkpoint = serde_json::from_str(&raw)?;
            ensure!(
                progress.completed_files == progress.total_files
                    && progress.total_fragments == Some(progress.completed_fragments),
                "RESULT_CORRUPT: incomplete aggregate completion"
            );
            let mut quality = spec.quality.clone();
            quality["final_for_request"] = json!(true);
            quality["coverage"]["input_coverage"] = progress.coverage();
            tx.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?)",
                params![spec.result_ref, head + 1, cutoff, 1, quality.to_string()],
            )?;
            tx.execute(
                "INSERT INTO checkpoints VALUES(?,?,?)",
                params![spec.result_ref, head + 1, cutoff - 1],
            )?;
            tx.execute(
                "UPDATE results SET head=? WHERE id=?",
                params![head + 1, spec.result_ref],
            )?;
            event(
                &tx,
                &spec.job_id,
                "result.ready",
                json!({"result_ref":spec.result_ref,"revision":head+1,"quality":quality}),
            )?;
        }
        if state == "completed" && spec.prepared.is_some() {
            crate::prepare::commit(&tx, spec)?;
        }
        tx.execute(
            "UPDATE jobs SET state=?,phase=?,error=?,metrics=?,finished=? WHERE id=?",
            params![
                state,
                state,
                error.as_ref().map(Value::to_string),
                metrics.to_string(),
                now_ms(),
                spec.job_id
            ],
        )?;
        event(
            &tx,
            &spec.job_id,
            &format!("job.{state}"),
            json!({"state":state,"result_ref":spec.result_ref,"error":error}),
        )?;
        #[cfg(test)]
        crate::publication_tests::fault("before_final_commit");
        tx.commit()?;
        #[cfg(test)]
        crate::publication_tests::fault("after_final_commit");
        self.changed.send_replace(());
        Ok(())
    }
    pub fn invalidate(&self, parent: &str) -> Result<()> {
        let mut c = self.conn.lock().unwrap();
        let tx = c.transaction()?;
        let mut ids = vec![parent.to_owned()];
        let mut pos = 0;
        while pos < ids.len() {
            let more = tx
                .prepare("SELECT child FROM deps WHERE parent=?")?
                .query_map([&ids[pos]], |r| r.get::<_, String>(0))?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            for id in more {
                if !ids.contains(&id) {
                    ids.push(id)
                }
            }
            pos += 1;
        }
        for id in ids {
            tx.execute(
                "UPDATE objects SET validity='source_changed' WHERE id=? AND validity!='expired'",
                [&id],
            )?;
            tx.execute(
                "UPDATE results SET validity='source_changed' WHERE id=? AND validity!='expired'",
                [&id],
            )?;
            let job: Option<String> = tx
                .query_row("SELECT job_id FROM results WHERE id=?", [&id], |r| r.get(0))
                .optional()?;
            if let Some(job) = job {
                event(
                    &tx,
                    &job,
                    "result.invalidated",
                    json!({"result_ref":id,"validity":"source_changed"}),
                )?;
            }
        }
        tx.commit()?;
        self.changed.send_replace(());
        Ok(())
    }
}
