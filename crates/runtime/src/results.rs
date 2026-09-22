use crate::db::Db;
use anyhow::{Result, bail, ensure};
use arrow::{
    array::*,
    datatypes::{DataType, Schema},
    ipc::reader::FileReader,
    util::display::{ArrayFormatter, FormatOptions},
};
use rowtrail_contracts::ReadParams;
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs::File, io::Read, path::PathBuf};

pub const INLINE_LIMIT: usize = 128 * 1024;

/// SQLite page/WAL overhead is outside the encoded result-byte counter.
pub fn verified_part(file: &crate::sources::SourceFile, checksum: &str) -> Result<Vec<u8>> {
    let Some(part) = &file.inline else {
        return verified_bytes(&file.path, file.size, checksum);
    };
    ensure!(
        file.size <= INLINE_LIMIT as u64,
        "RESULT_CORRUPT: oversized inline part"
    );
    let c = rusqlite::Connection::open_with_flags(
        &part.database,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    c.busy_timeout(std::time::Duration::from_secs(5))?;
    verified_inline(&c, part, file.size, checksum)
}

fn verified_inline(
    c: &rusqlite::Connection,
    part: &crate::sources::InlineSource,
    size: u64,
    checksum: &str,
) -> Result<Vec<u8>> {
    ensure!(
        size <= INLINE_LIMIT as u64,
        "RESULT_CORRUPT: oversized inline part"
    );
    let bytes: Option<Vec<u8>> = c
        .prepare_cached(
            "SELECT data FROM inline_parts WHERE result_id=? AND seq=? AND length(data)=?",
        )?
        .query_row(params![part.result_ref, part.seq, size], |r| r.get(0))
        .optional()?;
    let bytes =
        bytes.ok_or_else(|| anyhow::anyhow!("RESULT_CORRUPT: missing or resized inline part"))?;
    ensure!(
        hex::encode(Sha256::digest(&bytes)) == checksum,
        "RESULT_CORRUPT: checksum mismatch"
    );
    Ok(bytes)
}

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
    ensure!(validity != "expired", "OBJECT_EXPIRED: {result}");
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
    let start: u64 = c
        .query_row(
            "SELECT part_seq FROM checkpoints WHERE result_id=? AND revision=?",
            params![result, revision],
            |r| r.get(0),
        )
        .optional()?
        .unwrap_or(0);
    let parts = c
        .prepare(
            "SELECT path,bytes,checksum,rows,identity FROM parts WHERE result_id=? AND seq>=? AND seq<? ORDER BY seq",
        )?
        .query_map(params![result, start, cutoff], |r| {
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
        parts.len() == cutoff.saturating_sub(start) as usize,
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
pub fn verified_bytes(
    path: &std::path::Path,
    expected_bytes: u64,
    checksum: &str,
) -> Result<Vec<u8>> {
    ensure!(
        expected_bytes <= 8 * 1024 * 1024,
        "RESULT_CORRUPT: oversized part"
    );
    let f = File::open(path)
        .map_err(|e| anyhow::anyhow!("RESULT_UNAVAILABLE: {}: {e}", path.display()))?;
    ensure!(
        f.metadata()?.len() == expected_bytes,
        "RESULT_CORRUPT: size mismatch"
    );
    let mut bytes = Vec::with_capacity(expected_bytes as usize);
    f.take(expected_bytes + 1).read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() as u64 == expected_bytes,
        "RESULT_CORRUPT: size changed"
    );
    ensure!(
        hex::encode(Sha256::digest(&bytes)) == checksum,
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
fn value(array: &dyn Array, formatter: &ArrayFormatter<'_>, row: usize) -> Result<Value> {
    if array.is_null(row) {
        return Ok(Value::Null);
    }
    let formatted = formatter.value(row).to_string();
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
    let _guard = db.gc.read().unwrap();
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
    // The per-row changes are decimal counts, a boolean and a hex cursor.
    // Account for their exact encoded lengths instead of serializing the full
    // schema and quality once for every row. Final envelope checking stays below.
    let cursor_zero_bytes = serde_json::to_vec(&Cursor {
        store_id: db.store_id.clone(),
        result_ref: p.result_ref.clone(),
        revision: snap.revision,
        offset: 0,
        columns: p.columns.clone(),
    })?
    .len();
    response["next_cursor"] = Value::Null;
    response["presentation"] = json!({"returned_rows":0,"has_more":true});
    let metadata_bytes = serde_json::to_vec(&response)?.len();
    let mut position = 0;
    let mut returned = 0;
    let mut bytes_verified = 0;
    let mut bytes_read = 0;
    let mut inline_bytes = 0;
    let mut rows = Vec::new();
    let mut row_bytes = 0;
    'parts: for (_path, size, hash, part_rows, identity) in &snap.parts {
        if position + part_rows <= start {
            position += part_rows;
            continue;
        }
        if returned >= p.max_rows {
            break;
        }
        let bytes = if let Some(part) = &identity.inline {
            ensure!(
                part.database == db.workspace.join("metadata.sqlite"),
                "RESULT_CORRUPT: inline database identity"
            );
            verified_inline(&db.conn.lock().unwrap(), part, *size, hash)?
        } else {
            verified_part(identity, hash)?
        };
        bytes_verified += bytes.len() as u64;
        let reader = FileReader::try_new(std::io::Cursor::new(bytes), Some(indices.clone()))?;
        if identity.inline.is_some() {
            inline_bytes += size;
        } else {
            bytes_read += size;
        }
        for batch in reader {
            let batch = batch?;
            crate::numeric::validate_batch(&batch)?;
            let options = FormatOptions::default().with_display_error(true);
            let formatters = batch
                .columns()
                .iter()
                .map(|a| ArrayFormatter::try_new(a.as_ref(), &options))
                .collect::<std::result::Result<Vec<_>, _>>()?;
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
                    .zip(&formatters)
                    .map(|(a, f)| value(a.as_ref(), f, row))
                    .collect::<Result<Vec<_>>>()?;
                let row = json!(values);
                let added_bytes = serde_json::to_vec(&row)?.len() + usize::from(!rows.is_empty());
                let candidate_bytes = page_metadata_bytes(
                    metadata_bytes,
                    cursor_zero_bytes,
                    start + returned as u64 + 1,
                    returned + 1,
                    snap.rows,
                );
                if candidate_bytes + row_bytes + added_bytes + 256 > p.max_bytes {
                    ensure!(
                        returned > 0,
                        "OUTPUT_BUDGET_TOO_SMALL: one row does not fit"
                    );
                    break 'parts;
                }
                row_bytes += added_bytes;
                rows.push(row);
                returned += 1;
                position += 1;
            }
        }
    }
    response["presentation"] =
        json!({"returned_rows":returned,"has_more":start+(returned as u64)<snap.rows});
    response["next_cursor"] = cursor_for(start + returned as u64)?;
    response["rows"] = json!(rows);
    response["read_metrics"] = json!({"checksum_bytes":bytes_verified,"part_file_bytes_opened":bytes_read,"inline_bytes_loaded":inline_bytes});
    ensure!(
        serde_json::to_vec(&response)?.len() + 128 <= p.max_bytes,
        "OUTPUT_BUDGET_TOO_SMALL"
    );
    Ok(response)
}

fn page_metadata_bytes(
    base: usize,
    cursor_zero: usize,
    offset: u64,
    rows: usize,
    total: u64,
) -> usize {
    let digits = |n: u64| n.checked_ilog10().unwrap_or(0) as usize + 1;
    let cursor = if offset < total {
        // Two hex characters per byte, plus JSON string quotes.
        2 * (cursor_zero + digits(offset) - 1) + 2
    } else {
        4 // null
    };
    base + digits(rows as u64) - 1 + usize::from(offset >= total) + cursor - 4
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_byte_accounting_matches_json_at_decimal_boundaries() {
        let mut cursor = Cursor {
            store_id: "store_abc".into(),
            result_ref: "res_def".into(),
            revision: 19,
            offset: 0,
            columns: vec!["金额\n\"\\".into()],
        };
        let cursor_zero = serde_json::to_vec(&cursor).unwrap().len();
        let base = json!({"schema":[{"name":"金额\n\"\\","type":"Decimal128(20, 2)"}],"rows":[],"next_cursor":null,"presentation":{"returned_rows":0,"has_more":true}});
        let base_bytes = serde_json::to_vec(&base).unwrap().len();
        for offset in [0, 9, 10, 99, 100, 999, 1000, u64::MAX - 1, u64::MAX] {
            for rows in [0, 9, 10, 99, 100, 9999, 10000] {
                for total in [offset, u64::MAX] {
                    cursor.offset = offset;
                    let mut actual = base.clone();
                    actual["next_cursor"] = if offset < total {
                        json!(hex::encode(serde_json::to_vec(&cursor).unwrap()))
                    } else {
                        Value::Null
                    };
                    actual["presentation"] = json!({"returned_rows":rows,"has_more":offset<total});
                    assert_eq!(
                        page_metadata_bytes(base_bytes, cursor_zero, offset, rows, total),
                        serde_json::to_vec(&actual).unwrap().len()
                    );
                }
            }
        }
    }
}
