//! Explicit retention and conservative admission. The quota covers managed data
//! and reserved result/spill bytes; SQLite, logs and external exports are separate.
use crate::{db::Db, model::JobSpec};
use anyhow::{Result, ensure};
use rowtrail_contracts::{QueryParams, WorkspaceParams};
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};
use std::{collections::HashSet, path::Path};

fn usage(c: &Connection) -> Result<(u64, u64, u64)> {
    let stored = c.query_row("SELECT COALESCE(SUM(bytes),0) FROM parts", [], |r| r.get(0))?;
    let active: Vec<String> = c
        .prepare("SELECT spec FROM jobs WHERE state IN ('queued','running','stopping')")?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    let mut reserved = 0u64;
    for raw in active {
        let spec: JobSpec = serde_json::from_str(&raw)?;
        if let Some(q) = spec.query {
            reserved = reserved
                .saturating_add(q.execution.result_bytes)
                .saturating_add(q.execution.spill_bytes);
        }
    }
    let quota: String = c
        .query_row(
            "SELECT value FROM meta WHERE key='storage_quota_bytes'",
            [],
            |r| r.get(0),
        )
        .optional()?
        .unwrap_or_else(|| "0".into());
    Ok((stored, reserved, quota.parse()?))
}
pub fn admit(c: &Connection, query: Option<&QueryParams>) -> Result<()> {
    let Some(q) = query else { return Ok(()) };
    let configured: Option<String> = c
        .query_row(
            "SELECT value FROM meta WHERE key='storage_quota_bytes'",
            [],
            |r| r.get(0),
        )
        .optional()?;
    if configured.as_deref().is_none_or(|v| v == "0") {
        return Ok(());
    }
    let (stored, reserved, quota) = usage(c)?;
    if quota != 0 {
        ensure!(
            stored
                .saturating_add(reserved)
                .saturating_add(q.execution.result_bytes)
                .saturating_add(q.execution.spill_bytes)
                <= quota,
            "RESOURCE_EXHAUSTED: workspace quota; release unused results and run GC, or lower result/spill budgets"
        );
    }
    Ok(())
}
pub fn retain(db: &Db, reference: &str, pin: bool) -> Result<Value> {
    let _guard = db.gc.read().unwrap();
    let mut c = db.conn.lock().unwrap();
    let tx = c.transaction()?;
    let storage: Option<(String,String)> = tx.query_row(
        "WITH RECURSIVE parents(id) AS (SELECT ? UNION SELECT d.parent FROM deps d JOIN parents p ON d.child=p.id) SELECT r.id,r.validity FROM results r JOIN parents p ON p.id=r.id LIMIT 1", [reference], |r| Ok((r.get(0)?,r.get(1)?))).optional()?;
    let (object, validity) = storage.ok_or_else(|| {
        anyhow::anyhow!(
            "INVALID_ARGUMENT: retention requires a result or managed dataset reference"
        )
    })?;
    ensure!(validity != "expired", "OBJECT_EXPIRED: {reference}");
    tx.execute("INSERT INTO retention VALUES(?,?) ON CONFLICT(object) DO UPDATE SET released=excluded.released", params![object,!pin])?;
    if pin {
        tx.execute("INSERT OR IGNORE INTO pins VALUES(?,'user')", [&object])?;
    } else {
        tx.execute(
            "DELETE FROM pins WHERE object=? AND owner='user'",
            [&object],
        )?;
    }
    tx.commit()?;
    Ok(
        json!({"ref":reference,"storage_ref":object,"pinned":pin,"released":!pin,"reclamation":"explicit workspace gc; active and retained dependencies remain protected"}),
    )
}

pub fn workspace(db: &Db, p: WorkspaceParams) -> Result<Value> {
    if p.action == "summary" {
        return crate::catalog::summary(db, p);
    }
    ensure!(
        p.cursor.is_none() && p.kind.is_none(),
        "INVALID_ARGUMENT: cursor/kind are only for summary"
    );
    ensure!(
        matches!(p.action.as_str(), "usage" | "configure" | "gc"),
        "UNSUPPORTED_OPERATION: workspace action"
    );
    ensure!(
        p.action == "configure" || p.quota_bytes.is_none(),
        "INVALID_ARGUMENT: quota_bytes is only for configure"
    );
    let _guard = db.gc.write().unwrap();
    let mut c = db.conn.lock().unwrap();
    if p.action == "configure" {
        let quota = p.quota_bytes.ok_or_else(|| {
            anyhow::anyhow!("INVALID_ARGUMENT: configure requires quota_bytes (0 disables quota)")
        })?;
        let (stored, reserved, _) = usage(&c)?;
        ensure!(
            quota == 0 || quota >= stored.saturating_add(reserved),
            "RESOURCE_EXHAUSTED: quota is below stored plus reserved bytes"
        );
        c.execute("INSERT INTO meta VALUES('storage_quota_bytes',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [quota.to_string()])?;
    }
    let mut reclaimed = 0u64;
    let mut candidates = 0usize;
    if p.action == "gc" {
        let tx = c.transaction()?;
        // Retained results/pins and every queued/running job protect all ancestors.
        let roots: Vec<String> = tx.prepare("SELECT r.id FROM results r LEFT JOIN retention t ON t.object=r.id WHERE r.validity!='expired' AND COALESCE(t.released,0)=0 UNION SELECT object FROM pins UNION SELECT result_ref FROM jobs WHERE state IN ('queued','running','stopping')")?.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
        let mut protected = HashSet::new();
        let mut pending = roots;
        while let Some(id) = pending.pop() {
            if !protected.insert(id.clone()) {
                continue;
            }
            pending.extend(
                tx.prepare("SELECT parent FROM deps WHERE child=?")?
                    .query_map([id], |r| r.get::<_, String>(0))?
                    .collect::<rusqlite::Result<Vec<_>>>()?,
            );
        }
        let eligible: Vec<(String,u64)> = tx.prepare("SELECT r.id,COALESCE((SELECT SUM(bytes) FROM parts WHERE result_id=r.id),0) FROM results r JOIN jobs j ON j.id=r.job_id LEFT JOIN retention t ON t.object=r.id WHERE j.state NOT IN ('queued','running','stopping') AND (t.released=1 OR (r.validity='expired' AND COALESCE(t.released,0)!=2)) ORDER BY r.id")?.query_map([], |r| Ok((r.get(0)?,r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
        let selected = eligible
            .into_iter()
            .filter(|(id, _)| !protected.contains(id))
            .take(1000)
            .collect::<Vec<_>>();
        for (id, bytes) in &selected {
            candidates += 1;
            reclaimed += bytes;
            if !p.dry_run {
                tx.execute("UPDATE results SET validity='expired' WHERE id=?", [id])?;
                tx.execute("WITH RECURSIVE children(id) AS (SELECT ? UNION SELECT d.child FROM deps d JOIN children p ON d.parent=p.id) UPDATE objects SET validity='expired' WHERE id IN children", [id])?;
            }
        }
        // Tombstones become durable before unlinking any file. Interrupted GC is
        // safe to retry; expired references can never silently read an empty result.
        tx.commit()?;
        if !p.dry_run {
            for (id, _) in &selected {
                remove_directory(&db.workspace.join("store").join(id))?;
                c.execute("DELETE FROM parts WHERE result_id=?", [id])?;
                c.execute("INSERT INTO retention VALUES(?,2) ON CONFLICT(object) DO UPDATE SET released=2", [id])?;
            }
            clean_temporary(db, &c)?;
        }
    }
    let (stored, reserved, quota) = usage(&c)?;
    Ok(
        json!({"stored_bytes":stored,"reserved_bytes":reserved,"quota_bytes":quota,"quota_scope":"managed result/prepare data plus conservative result/spill reservations; excludes metadata, logs, and external files","gc":if p.action=="gc" { json!({"dry_run":p.dry_run,"candidate_count":candidates,"candidate_bytes":reclaimed,"reclaimed_bytes":if p.dry_run {0}else{reclaimed},"batch_limit":1000}) } else {Value::Null}}),
    )
}
fn remove_directory(path: &Path) -> Result<()> {
    match std::fs::remove_dir_all(path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.into()),
    }
}
fn clean_temporary(db: &Db, c: &Connection) -> Result<()> {
    let active: HashSet<String> = c
        .prepare("SELECT attempt FROM jobs WHERE state IN ('queued','running','stopping')")?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    for root in ["staging", "spill"] {
        for entry in std::fs::read_dir(db.workspace.join(root))?.take(1000) {
            let entry = entry?;
            if !active.contains(&entry.file_name().to_string_lossy().to_string()) {
                remove_directory(&entry.path())?;
            }
        }
    }
    Ok(())
}

/// Called only after execution has stopped. A rename preceding a crashed SQLite
/// commit can leave an unindexed file; it must never become a readable revision.
pub fn clean_attempt(db: &Db, spec: &JobSpec) -> Result<()> {
    for root in ["staging", "spill"] {
        remove_directory(&db.workspace.join(root).join(&spec.attempt))?;
    }
    let c = db.conn.lock().unwrap();
    let known: HashSet<String> = c
        .prepare("SELECT path FROM parts WHERE result_id=?")?
        .query_map([&spec.result_ref], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    let dir = db.workspace.join("store").join(&spec.result_ref);
    if dir.is_dir() {
        for entry in std::fs::read_dir(&dir)? {
            let entry = entry?;
            if !known.contains(&entry.path().to_string_lossy().to_string()) {
                std::fs::remove_file(entry.path())?;
            }
        }
    }
    Ok(())
}
