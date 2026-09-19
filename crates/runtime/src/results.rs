use crate::db::Db;
use anyhow::{Result, bail, ensure};
use arrow::{
    array::*,
    datatypes::{DataType, Schema},
    ipc::reader::FileReader,
};
use rowtrail_contracts::ReadParams;
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs::File, io::Read, path::PathBuf};

pub struct Snapshot {
    pub schema: Schema,
    pub quality: Value,
    pub revision: u64,
    pub rows: u64,
    pub parts: Vec<(PathBuf, u64, String, u64, crate::sources::SourceFile)>,
    pub validity: String,
    pub job_state: String,
}
pub fn snapshot(db: &Db, result: &str, revision: Option<u64>) -> Result<Snapshot> {
    let c = db.conn.lock().unwrap();
    let raw:Option<(Option<String>,u64,String,String)>=c.query_row("SELECT r.schema,r.head,r.validity,j.state FROM results r JOIN jobs j ON j.id=r.job_id WHERE r.id=?",[result],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?))).optional()?;
    let (schema, head, validity, job_state) =
        raw.ok_or_else(|| anyhow::anyhow!("OBJECT_NOT_FOUND: {result}"))?;
    let revision = revision.unwrap_or(head);
    ensure!(revision > 0, "RESULT_NOT_READY: no committed revision");
    let row: Option<(u64, u64, String)> = c
        .query_row(
            "SELECT cutoff,rows,quality FROM revisions WHERE result_id=? AND revision=?",
            params![result, revision],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .optional()?;
    let (cutoff, rows, quality) =
        row.ok_or_else(|| anyhow::anyhow!("OBJECT_NOT_FOUND: revision {revision}"))?;
    let parts = c
        .prepare(
            "SELECT path,bytes,checksum,rows,identity FROM parts WHERE result_id=? AND seq<? ORDER BY seq",
        )?
        .query_map(params![result, cutoff], |r| {
            Ok((
                PathBuf::from(r.get::<_, String>(0)?),
                r.get(1)?,
                r.get(2)?,
                r.get(3)?,
                r.get::<_,String>(4)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
        .into_iter().map(|(path,size,hash,rows,identity)|Ok((path,size,hash,rows,serde_json::from_str(&identity)?))).collect::<Result<Vec<_>>>()?;
    ensure!(
        parts.len() == cutoff as usize,
        "RESULT_CORRUPT: missing part metadata"
    );
    Ok(Snapshot {
        schema: serde_json::from_str(&schema.ok_or_else(|| anyhow::anyhow!("RESULT_NOT_READY"))?)?,
        quality: serde_json::from_str(&quality)?,
        revision,
        rows,
        parts,
        validity,
        job_state,
    })
}
pub fn verify(path: &std::path::Path, expected_bytes: u64, checksum: &str) -> Result<u64> {
    let mut f = File::open(path)
        .map_err(|e| anyhow::anyhow!("RESULT_UNAVAILABLE: {}: {e}", path.display()))?;
    ensure!(
        f.metadata()?.len() == expected_bytes,
        "RESULT_CORRUPT: size mismatch"
    );
    let mut hasher = Sha256::new();
    let mut b = [0; 65536];
    let mut bytes = 0;
    loop {
        let n = f.read(&mut b)?;
        if n == 0 {
            break;
        }
        hasher.update(&b[..n]);
        bytes += n as u64;
    }
    ensure!(
        hex::encode(hasher.finalize()) == checksum,
        "RESULT_CORRUPT: checksum mismatch"
    );
    Ok(bytes)
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cursor {
    store_id: String,
    result_ref: String,
    revision: u64,
    offset: u64,
    columns: Vec<String>,
}
fn value(array: &dyn Array, row: usize) -> Result<Value> {
    if array.is_null(row) {
        return Ok(Value::Null);
    }
    let formatted = arrow::util::display::array_value_to_string(array, row)?;
    match array.data_type() {
        DataType::Int8
        | DataType::Int16
        | DataType::Int32
        | DataType::UInt8
        | DataType::UInt16
        | DataType::UInt32
        | DataType::Boolean => Ok(serde_json::from_str(&formatted)?),
        DataType::Float16 | DataType::Float32 | DataType::Float64 => Ok(serde_json::from_str(
            &formatted,
        )
        .unwrap_or_else(|_| json!({"type":array.data_type().to_string(),"value":formatted}))),
        DataType::Binary
        | DataType::LargeBinary
        | DataType::BinaryView
        | DataType::FixedSizeBinary(_) => {
            bail!("UNSUPPORTED_OPERATION: binary cells require Arrow/Parquet export")
        }
        _ => Ok(json!(formatted)),
    }
}
pub fn read(db: &Db, p: &ReadParams) -> Result<Value> {
    ensure!(
        p.max_bytes <= rowtrail_contracts::FRAME_LIMIT - 512 && p.max_rows <= 10000,
        "INVALID_ARGUMENT: output exceeds capability limits"
    );
    let cursor = p
        .cursor
        .as_ref()
        .map(|s| {
            hex::decode(s)
                .map_err(anyhow::Error::from)
                .and_then(|v| serde_json::from_slice::<Cursor>(&v).map_err(anyhow::Error::from))
        })
        .transpose()?;
    if let Some(c) = &cursor {
        ensure!(
            c.store_id == db.store_id
                && c.result_ref == p.result_ref
                && c.columns == p.columns
                && p.revision.is_none_or(|r| r == c.revision),
            "INVALID_CURSOR: cursor does not match workspace, revision, or columns"
        )
    }
    let snap = snapshot(
        db,
        &p.result_ref,
        cursor.as_ref().map(|c| c.revision).or(p.revision),
    )?;
    ensure!(
        snap.validity == "valid",
        "SOURCE_CHANGED: result invalidated"
    );
    let start = cursor.map(|c| c.offset).unwrap_or(0);
    ensure!(
        start <= snap.rows,
        "INVALID_CURSOR: offset outside revision"
    );
    let indices = if p.columns.is_empty() {
        (0..snap.schema.fields().len()).collect::<Vec<_>>()
    } else {
        p.columns
            .iter()
            .map(|c| snap.schema.index_of(c))
            .collect::<std::result::Result<Vec<_>, _>>()?
    };
    let schema = indices
        .iter()
        .map(|i| {
            let f = snap.schema.field(*i);
            json!({"name":f.name(),"type":f.data_type().to_string(),"nullable":f.is_nullable()})
        })
        .collect::<Vec<_>>();
    let mut response = json!({"result_ref":p.result_ref,"revision":snap.revision,"schema":schema,"rows":[],"quality":snap.quality,"validity":snap.validity,"job_state":snap.job_state,"presentation":{"returned_rows":0,"has_more":start<snap.rows},"next_cursor":null});
    let cursor_for = |offset: u64| -> Result<Value> {
        if offset >= snap.rows {
            Ok(Value::Null)
        } else {
            Ok(json!(hex::encode(serde_json::to_vec(&Cursor {
                store_id: db.store_id.clone(),
                result_ref: p.result_ref.clone(),
                revision: snap.revision,
                offset,
                columns: p.columns.clone()
            })?)))
        }
    };
    response["next_cursor"] = cursor_for(start)?;
    ensure!(
        serde_json::to_vec(&response)?.len() + 256 <= p.max_bytes,
        "OUTPUT_BUDGET_TOO_SMALL: metadata does not fit"
    );
    let mut position = 0;
    let mut returned = 0;
    let mut bytes_verified = 0;
    let mut bytes_read = 0;
    'parts: for (path, size, hash, part_rows, _identity) in &snap.parts {
        if position + part_rows <= start {
            position += part_rows;
            continue;
        }
        if returned >= p.max_rows {
            break;
        }
        bytes_verified += verify(path, *size, hash)?;
        let reader = FileReader::try_new(File::open(path)?, Some(indices.clone()))?;
        bytes_read += size;
        for batch in reader {
            let batch = batch?;
            for row in 0..batch.num_rows() {
                if position < start {
                    position += 1;
                    continue;
                }
                if returned >= p.max_rows {
                    break 'parts;
                }
                let values = batch
                    .columns()
                    .iter()
                    .map(|a| value(a.as_ref(), row))
                    .collect::<Result<Vec<_>>>()?;
                response["rows"].as_array_mut().unwrap().push(json!(values));
                response["next_cursor"] = cursor_for(start + returned as u64 + 1)?;
                response["presentation"] = json!({"returned_rows":returned+1,"has_more":start+returned as u64+1<snap.rows});
                if serde_json::to_vec(&response)?.len() + 256 > p.max_bytes {
                    response["rows"].as_array_mut().unwrap().pop();
                    ensure!(
                        returned > 0,
                        "OUTPUT_BUDGET_TOO_SMALL: one row does not fit"
                    );
                    break 'parts;
                }
                returned += 1;
                position += 1;
            }
        }
    }
    response["presentation"] =
        json!({"returned_rows":returned,"has_more":start+(returned as u64)<snap.rows});
    response["next_cursor"] = cursor_for(start + returned as u64)?;
    response["read_metrics"] =
        json!({"checksum_bytes":bytes_verified,"part_file_bytes_opened":bytes_read});
    ensure!(
        serde_json::to_vec(&response)?.len() + 128 <= p.max_bytes,
        "OUTPUT_BUDGET_TOO_SMALL"
    );
    Ok(response)
}
