use crate::{
    db::event,
    model::JobSpec,
    sources::{Manifest, SourceFile},
};
use anyhow::{Result, ensure};
use rusqlite::{Connection, params};
use serde_json::json;

/// Visibility changes only in the terminal SQLite transaction. Failed/cancelled
/// conversions never publish a dataset, even if some Parquet parts were sealed.
pub fn commit(c: &Connection, spec: &JobSpec) -> Result<()> {
    let prepared = spec.prepared.as_ref().unwrap();
    let raw: String = c.query_row(
        "SELECT schema FROM results WHERE id=?",
        [&spec.result_ref],
        |r| r.get(0),
    )?;
    let mut schema: arrow::datatypes::Schema = serde_json::from_str(&raw)?;
    let mut quality = spec.quality.clone();
    quality["final_for_request"] = json!(true);
    quality["source_consistency"] = json!("immutable_materialized");
    let mut metadata = schema.metadata().clone();
    metadata.insert("rowtrail.quality".into(), quality.to_string());
    schema = schema.with_metadata(metadata);
    let files = c
        .prepare("SELECT identity,checksum,rows FROM parts WHERE result_id=? ORDER BY seq")?
        .query_map([&spec.result_ref], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, u64>(2)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
        .into_iter()
        .map(|(raw, hash, rows)| {
            let mut file: SourceFile = serde_json::from_str(&raw)?;
            file.checksum = Some(hash);
            file.rows = Some(rows);
            Ok(file)
        })
        .collect::<Result<Vec<_>>>()?;
    ensure!(
        !files.is_empty(),
        "RESULT_CORRUPT: prepare completed without a Parquet part"
    );
    let source = spec.workspace.join("store").join(&spec.result_ref);
    let m = Manifest {
        id: prepared.manifest_ref.clone(),
        dataset_ref: prepared.dataset_ref.clone(),
        source,
        format: "parquet".into(),
        schema,
        files,
        header: false,
        delimiter: b',',
        schema_origin: "prepared".into(),
        metadata_bytes: 0,
        inferred_rows: 0,
    };
    let encoded = serde_json::to_string(&m)?;
    ensure!(
        encoded.len() <= rowtrail_contracts::FRAME_LIMIT / 2,
        "RESOURCE_EXHAUSTED: prepared manifest metadata limit"
    );
    c.execute("INSERT INTO objects(id,kind,data) VALUES(?,'dataset',?)", params![m.dataset_ref,json!({"dataset_ref":m.dataset_ref,"manifest_ref":m.id,"storage_ref":spec.result_ref,"managed":true}).to_string()])?;
    c.execute(
        "INSERT INTO objects(id,kind,data) VALUES(?,'manifest',?)",
        params![m.id, encoded],
    )?;
    // A finished managed copy survives changes to its original source.
    c.execute("DELETE FROM deps WHERE child=?", [&spec.result_ref])?;
    c.execute(
        "INSERT INTO deps VALUES(?,?)",
        params![m.dataset_ref, spec.result_ref],
    )?;
    c.execute("INSERT INTO deps VALUES(?,?)", params![m.id, m.dataset_ref])?;
    event(
        c,
        &spec.job_id,
        "dataset.ready",
        json!({"dataset_ref":m.dataset_ref,"manifest_ref":m.id,"quality":quality}),
    )?;
    Ok(())
}
