//! A bounded metadata-only inventory for a fresh agent connection.
use crate::{db::Db, model::Checkpoint};
use anyhow::{Result, ensure};
use rowtrail_contracts::{FRAME_LIMIT, WorkspaceParams, terminal};
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cursor {
    store: String,
    kind: Option<String>,
    bounds: [i64; 3],
    after: (String, i64),
}
pub fn summary(db: &Db, p: WorkspaceParams) -> Result<Value> {
    ensure!(
        p.quota_bytes.is_none(),
        "INVALID_ARGUMENT: quota_bytes is only for configure"
    );
    ensure!(
        (1..=1000).contains(&p.limit) && (1024..=FRAME_LIMIT - 512).contains(&p.max_bytes),
        "INVALID_ARGUMENT: summary limit/byte budget"
    );
    ensure!(
        p.kind
            .as_deref()
            .is_none_or(|k| matches!(k, "dataset" | "job" | "result")),
        "INVALID_ARGUMENT: kind must be dataset, job or result"
    );
    let _guard = db.gc.read().unwrap();
    let mut c = db.conn.lock().unwrap();
    let tx = c.transaction()?;
    let bounds=tx.query_row("SELECT (SELECT COALESCE(MAX(rowid),0) FROM objects),(SELECT COALESCE(MAX(rowid),0) FROM jobs),(SELECT COALESCE(MAX(rowid),0) FROM results)",[],|r|Ok([r.get(0)?,r.get(1)?,r.get(2)?]))?;
    let mut cursor = if let Some(raw) = p.cursor {
        ensure!(raw.len() <= 2048, "INVALID_CURSOR");
        let value: Cursor = serde_json::from_slice(
            &hex::decode(raw).map_err(|_| anyhow::anyhow!("INVALID_CURSOR"))?,
        )
        .map_err(|_| anyhow::anyhow!("INVALID_CURSOR"))?;
        ensure!(
            value.store == db.store_id
                && value.kind == p.kind
                && value
                    .bounds
                    .iter()
                    .zip(bounds)
                    .all(|(a, b)| *a >= 0 && *a <= b)
                && matches!(value.after.0.as_str(), "dataset" | "job" | "result")
                && value.after.1 >= 0,
            "INVALID_CURSOR"
        );
        value
    } else {
        Cursor {
            store: db.store_id.clone(),
            kind: p.kind,
            bounds,
            after: (String::new(), 0),
        }
    };
    let rows: Vec<(String, String, i64)> = tx
        .prepare(
            "SELECT k,id,n FROM (
        SELECT 'dataset' k,id,rowid n FROM objects WHERE kind='dataset' AND rowid<=?1
        UNION ALL SELECT 'job',id,rowid FROM jobs WHERE rowid<=?2
        UNION ALL SELECT 'result',id,rowid FROM results WHERE rowid<=?3)
        WHERE (k>?4 OR (k=?4 AND n>?5)) AND (?6 IS NULL OR k=?6) ORDER BY k,n LIMIT ?7",
        )?
        .query_map(
            params![
                cursor.bounds[0],
                cursor.bounds[1],
                cursor.bounds[2],
                cursor.after.0,
                cursor.after.1,
                cursor.kind,
                p.limit + 1
            ],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?
        .collect::<rusqlite::Result<_>>()?;
    let counts=tx.query_row("SELECT (SELECT COUNT(*) FROM objects WHERE kind='dataset'),(SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','stopping')),(SELECT COUNT(*) FROM results WHERE head>0 AND validity='valid')",[],|r|Ok(json!({"datasets":r.get::<_,u64>(0)?,"active_jobs":r.get::<_,u64>(1)?,"readable_results":r.get::<_,u64>(2)?})))?;
    let mut out = json!({"store_id":db.store_id,"counts":counts,"items":[],"next_cursor":null,"membership":"fixed at first page; values observed per page","validity_check":"stored only; source identities checked when bindings are used"});
    let mut has_more = false;
    for (index, (kind, id, rowid)) in rows.iter().enumerate() {
        if index == p.limit {
            has_more = true;
            break;
        }
        let item = match kind.as_str() {
            "dataset" => {
                let (data, validity): (String, String) =
                    tx.query_row("SELECT data,validity FROM objects WHERE id=?", [id], |r| {
                        Ok((r.get(0)?, r.get(1)?))
                    })?;
                let data: Value = serde_json::from_str(&data)?;
                json!({"kind":kind,"ref":id,"binding":{"dataset_ref":id,"manifest_ref":data["manifest_ref"]},"stored_validity":validity,"managed":data["managed"]==true,"next_actions":if validity=="valid"{vec!["inspect","query"]}else{vec![]}})
            }
            "result" => {
                let (head,validity,quality,scope):(u64,String,Option<String>,String)=tx.query_row("SELECT r.head,r.validity,v.quality,j.scope_ref FROM results r JOIN jobs j ON j.id=r.job_id LEFT JOIN revisions v ON v.result_id=r.id AND v.revision=r.head WHERE r.id=?",[id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?)))?;
                let quality: Value = quality
                    .map(|q| serde_json::from_str(&q))
                    .transpose()?
                    .unwrap_or(Value::Null);
                json!({"kind":kind,"ref":id,"binding":if head>0 {json!({"result_ref":id,"revision":head})}else{Value::Null},"stored_validity":validity,"scope_ref":scope,"quality":{"accuracy":quality["accuracy"],"coverage":quality["coverage"],"final_for_request":quality["final_for_request"]},"next_actions":if head>0 && validity=="valid" {vec!["read","query","export","release"]}else{vec![]}})
            }
            _ => {
                let (state,result,head,error):(String,String,u64,Option<String>)=tx.query_row("SELECT j.state,j.result_ref,r.head,json_extract(j.error,'$.code') FROM jobs j JOIN results r ON r.id=j.result_ref WHERE j.id=?",[id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?)))?;
                let progress: Option<String> = tx
                    .query_row(
                        "SELECT progress FROM analysis_progress WHERE result_id=?",
                        [&result],
                        |r| r.get(0),
                    )
                    .optional()?;
                let progress = progress
                    .map(|raw| serde_json::from_str::<Checkpoint>(&raw).map(|c| c.coverage()))
                    .transpose()?;
                json!({"kind":kind,"ref":id,"state":state,"result_ref":result,"readable_revision":if head>0{Some(head)}else{None},"error_code":error,"input_coverage":progress,"next_actions":if terminal(&state){vec!["status"]}else{vec!["wait","cancel"]}})
            }
        };
        let old_after = cursor.after.clone();
        cursor.after = (kind.clone(), *rowid);
        out["items"].as_array_mut().unwrap().push(item);
        out["next_cursor"] = json!(hex::encode(serde_json::to_vec(&cursor)?));
        if serde_json::to_vec(&out)?.len() + 256 > p.max_bytes {
            out["items"].as_array_mut().unwrap().pop();
            cursor.after = old_after;
            has_more = true;
            break;
        }
    }
    ensure!(
        !has_more || !out["items"].as_array().unwrap().is_empty(),
        "OUTPUT_BUDGET_TOO_SMALL: summary entry and cursor need a larger max_bytes"
    );
    out["next_cursor"] = if has_more {
        json!(hex::encode(serde_json::to_vec(&cursor)?))
    } else {
        Value::Null
    };
    tx.commit()?;
    Ok(out)
}
