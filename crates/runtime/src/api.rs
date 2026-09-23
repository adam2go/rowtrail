use crate::{
    db::{Db, event},
    model::{Input, JobSpec},
    results,
    sources::Manifest,
};
use anyhow::{Result, bail, ensure};
use rowtrail_contracts::*;
use rusqlite::{OptionalExtension, params};
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, path::Path, sync::Arc};

fn decode<T: DeserializeOwned>(v: &Value) -> Result<T> {
    serde_json::from_value(v.clone()).map_err(|e| anyhow::anyhow!("INVALID_ARGUMENT: {e}"))
}
fn request_hash(req: &Request) -> String {
    let mut p = if req.method == "prepare" {
        serde_json::from_value::<PrepareParams>(req.params.clone())
            .ok()
            .and_then(|p| serde_json::to_value(p).ok())
            .unwrap_or_else(|| req.params.clone())
    } else {
        req.params.clone()
    };
    if let Some(o) = p.as_object_mut() {
        o.remove("output");
        if req.method == "inspect" {
            let head = o
                .get("checks")
                .and_then(Value::as_array)
                .is_some_and(|checks| checks.iter().any(|v| v == "head"));
            if let Some(budget) = o.get_mut("budget").and_then(Value::as_object_mut) {
                budget.remove("max_bytes");
                if !head {
                    budget.remove("max_rows");
                }
            }
        }
        if let Some(e) = o.get_mut("execution").and_then(Value::as_object_mut) {
            e.remove("wait_ms");
            e.remove("output");
        }
    }
    fn canonical(v: Value) -> Value {
        match v {
            Value::Object(o) => Value::Object(
                o.into_iter()
                    .collect::<BTreeMap<_, _>>()
                    .into_iter()
                    .map(|(k, v)| (k, canonical(v)))
                    .collect(),
            ),
            Value::Array(a) => Value::Array(a.into_iter().map(canonical).collect()),
            v => v,
        }
    }
    hex::encode(Sha256::digest(
        canonical(json!({"api_version":req.api_version,"method":req.method,"params":p}))
            .to_string(),
    ))
}
fn existing(c: &rusqlite::Connection, req: &Request) -> Result<Option<Value>> {
    let Some(key) = &req.idempotency_key else {
        return Ok(None);
    };
    ensure!(
        !key.is_empty() && key.len() <= 256,
        "INVALID_ARGUMENT: idempotency key length"
    );
    let row: Option<(String, String)> = c
        .query_row(
            "SELECT hash,response FROM idempotency WHERE key=?",
            [key],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    row.map(|(hash, v)| {
        ensure!(hash == request_hash(req), "IDEMPOTENCY_CONFLICT");
        Ok(serde_json::from_str(&v)?)
    })
    .transpose()
}
fn remember(c: &rusqlite::Connection, req: &Request, v: &Value) -> Result<()> {
    if let Some(key) = &req.idempotency_key {
        c.execute(
            "INSERT INTO idempotency VALUES(?,?,?)",
            params![key, request_hash(req), v.to_string()],
        )?;
    }
    Ok(())
}
pub fn capabilities() -> Value {
    json!({"api_version":"1","metadata_schema":"9; one-way upgrade from 3/4/5/6/7/8","numeric":crate::numeric::policy(),"progressive_aggregation":{"fragment_unit":"parquet_row_group","fragment_units":["parquet_row_group","manifest_file"],"checkpoint_interval_ms":50,"aggregate_limit":16,"numeric_inputs":"Int64, UInt64, Decimal128 scale 0..6","sum":"Decimal128(38,input_scale)","avg":"Decimal128(38,6), truncation toward zero","group_by":false,"sampling":false},"formats":["csv","tsv","parquet"],"source":"local","sql":"DataFusion 55.0.0 read-only SELECT; explicit bindings","analysis":["inspect:null_count","inspect:min_max","inspect:top_k","analyze:count","analyze:sum","analyze:avg"],"entries":["cli","ndjson_session","mcp_stdio","rust_sdk"],"workspace_summary":"bounded metadata catalog with exact label filter, fixed bindings, row counts and bounded field hints; store-scoped cursor; no source scan","query_parallelism":{"auto_max":4,"serial_below_input_bytes":16777216,"pool_bytes_per_parallel_partition":67108864,"explicit_target_partitions":"1..8; requires 64 MiB per partition when greater than one","target_not_thread_limit":true},"prepare":"explicit streaming CSV/TSV to managed Parquet; atomic dataset visibility","snapshot":"explicit independent managed Parquet copy of a dataset or final exact complete result; caller provenance stored, never executed","workspace_quota":"managed data plus conservative result/spill reservations; excludes SQLite/WAL overhead, logs and external exports","saved_result_scan":"whole IPC parts; separate files remain parallel; each new job verifies again","verified_part_cache_bytes":8388608,"result_part_target_bytes":6291456,"result_part_target_basis":"encoded IPC plus next batch array memory; prepared Parquet keeps input-memory target","inline_result_part_max_bytes":131072,"result_part_max_bytes":8388608,"native_tasks":false,"automatic_resume":false,"source_consistency":"best_effort","result_storage":"immutable Arrow IPC; parts up to 128 KiB in SQLite, larger parts in files","max_frame_bytes":FRAME_LIMIT,"minimum_error_budget_bytes":512,"max_output_bytes":FRAME_LIMIT-512,"max_output_rows":10000,"max_manifest_files":128,"max_directory_entries":4096,"default_parallel_jobs":1,"cancellation":"cooperative signal with forced worker termination after 300ms","engine_memory_limit":"MemoryPool; not RSS hard limit","scan_limit":"byte reservation before local reads","result_retention":"retained by default; explicit pin/release and dependency-safe workspace gc","event_retention":"until workspace removal; no event pruning yet","unsupported":["Windows","remote","sampling","native_tasks","automatic_resume","union_by_name","binary_inline","RSS_hard_limit"]})
}
fn validate_execution(e: &Execution) -> Result<()> {
    if let Some(n) = e.target_partitions {
        ensure!(
            (1..=8).contains(&n) && (n == 1 || e.memory_bytes / n >= 64 * 1024 * 1024),
            "INVALID_ARGUMENT: target_partitions must be 1..8; multiple partitions require at least 64 MiB of engine pool per partition"
        );
    }
    ensure!(
        e.wait_ms <= 30000 && e.run_timeout_ms > 0 && e.run_timeout_ms <= 24 * 3600 * 1000,
        "INVALID_ARGUMENT: execution time bounds"
    );
    ensure!(
        (8 * 1024 * 1024..=2 * 1024 * 1024 * 1024).contains(&e.memory_bytes),
        "INVALID_ARGUMENT: memory_bytes must be 8MiB..2GiB"
    );
    ensure!(
        e.scan_bytes > 0 && e.result_bytes > 0 && e.spill_bytes > 0,
        "INVALID_ARGUMENT: resource budgets must be positive"
    );
    ensure!(
        e.output.max_rows <= 10000 && (1024..=FRAME_LIMIT - 512).contains(&e.output.max_bytes),
        "INVALID_ARGUMENT: output budget"
    );
    Ok(())
}

fn validate_provenance(p: Option<&Provenance>) -> Result<()> {
    if let Some(p) = p {
        ensure!(
            p.description.len() <= 4096 && p.origin.len() <= 4096 && p.code.len() <= 16384,
            "INVALID_ARGUMENT: provenance description/origin limit 4096 UTF-8 bytes, code 16384 bytes"
        );
    }
    Ok(())
}

fn output_limit(req: &Request) -> usize {
    ((match req.method.as_str() {
        "query" | "analyze" => req
            .params
            .pointer("/execution/output/max_bytes")
            .and_then(Value::as_u64)
            .unwrap_or(8192),
        "open" => req
            .params
            .pointer("/output/max_bytes")
            .and_then(Value::as_u64)
            .unwrap_or(8192),
        "read" => req
            .params
            .get("max_bytes")
            .and_then(Value::as_u64)
            .unwrap_or(8192),
        "inspect" => req
            .params
            .pointer("/budget/max_bytes")
            .and_then(Value::as_u64)
            .unwrap_or(8192),
        "events" | "workspace" => req
            .params
            .get("max_bytes")
            .and_then(Value::as_u64)
            .unwrap_or(8192),
        _ => 65536,
    }) as usize)
        .min(FRAME_LIMIT - 512)
}

pub async fn dispatch(db: Arc<Db>, req: Request) -> Response {
    // An error needs a small envelope even when the requested observation cannot fit.
    let limit = output_limit(&req).clamp(512, FRAME_LIMIT - 512);
    let mut response = dispatch_inner(db, req).await;
    if let Some(error) = response.error.as_mut()
        && response.request_id.len() > 128
    {
        let mut end = 128;
        while !response.request_id.is_char_boundary(end) {
            end -= 1;
        }
        response.request_id.truncate(end);
        error.details["request_id_truncated"] = json!(true);
    }
    while serde_json::to_vec(&response).is_ok_and(|v| v.len() > limit) {
        let Some(error) = response.error.as_mut() else {
            break;
        };
        if error.message.is_empty() {
            error.details = json!({"message_truncated":true});
            if response.request_id.len() > 32 {
                let mut end = 32;
                while !response.request_id.is_char_boundary(end) {
                    end -= 1;
                }
                response.request_id.truncate(end);
                error.details["request_id_truncated"] = json!(true);
            }
        } else {
            let mut end = error.message.len() / 2;
            while !error.message.is_char_boundary(end) {
                end -= 1;
            }
            error.message.truncate(end);
            error.details["message_truncated"] = json!(true);
        }
    }
    response
}

async fn dispatch_inner(db: Arc<Db>, req: Request) -> Response {
    if req.api_version != API_VERSION {
        return Response::failure(
            &req,
            ApiError::new("PROTOCOL_VERSION_MISMATCH", "expected API version 1"),
        );
    }
    if req.request_id.len() > 128 {
        return Response::failure(
            &req,
            ApiError::new("INVALID_ARGUMENT", "request_id too long"),
        );
    }
    let outcome = handle(db, &req).await;
    match outcome {
        Ok(mut v) => {
            let limit = output_limit(&req);
            let fits = |value: &Value| {
                serde_json::to_vec(&Response::success(&req, value.clone()))
                    .is_ok_and(|b| b.len() <= limit)
            };
            if !fits(&v) && v.get("job").is_some() {
                v["job"]["metrics"] = Value::Null;
            }
            if !fits(&v) && v.get("observation").is_some() {
                v["observation"] = Value::Null;
                v["observation_omitted"] = json!("output_budget");
                if let Some(actions) = v["next_actions"].as_array_mut()
                    && !actions.contains(&json!("read"))
                {
                    actions.insert(0, json!("read"));
                }
            }
            if !fits(&v) {
                let mut error = ApiError::new(
                    "OUTPUT_BUDGET_TOO_SMALL",
                    "response metadata does not fit output budget",
                );
                error.details = json!({"job_id":v.get("job_id"),"result_ref":v.get("result_ref"),"accepted":v.get("job").is_some()});
                return Response::failure(&req, error);
            }
            Response::success(&req, v)
        }
        Err(e) => {
            let message = format!("{e:#}");
            let code = crate::errors::code(&e, "INTERNAL_ERROR");
            let mut error = ApiError::new(code, message);
            let details = crate::errors::details(&e);
            if details != json!({}) {
                error.details = details;
            }
            Response::failure(&req, error)
        }
    }
}
async fn handle(db: Arc<Db>, req: &Request) -> Result<Value> {
    match req.method.as_str() {
        "handshake" | "doctor" => Ok(
            json!({"store_id":db.store_id,"coordinator_pid":std::process::id(),"capabilities":capabilities(),"workspace":db.workspace,"runtime_version":env!("CARGO_PKG_VERSION")}),
        ),
        "open" => {
            let p: OpenParams = decode(&req.params)?;
            crate::catalog::validate_label(p.label.as_deref())?;
            let prior = { existing(&db.conn.lock().unwrap(), req)? };
            if let Some(v) = prior {
                return open_response(&db, v["manifest_ref"].as_str().unwrap(), &p.output);
            }
            let manifest = tokio::task::spawn_blocking(move || crate::sources::open(&p)).await??;
            let output: OpenParams = decode(&req.params)?;
            let refs = json!({"dataset_ref":manifest.dataset_ref,"manifest_ref":manifest.id});
            {
                let mut c = db.conn.lock().unwrap();
                let tx = c.transaction()?;
                if let Some(v) = existing(&tx, req)? {
                    drop(tx);
                    drop(c);
                    return open_response(&db, v["manifest_ref"].as_str().unwrap(), &output.output);
                }
                tx.execute("INSERT INTO objects(id,kind,data) VALUES(?,'dataset',?)",params![manifest.dataset_ref,json!({"dataset_ref":manifest.dataset_ref,"manifest_ref":manifest.id,"source":manifest.source,"label":manifest.label}).to_string()])?;
                if let Some(label) = &manifest.label {
                    tx.execute(
                        "INSERT INTO catalog_labels VALUES(?,?)",
                        params![manifest.dataset_ref, label],
                    )?;
                }
                tx.execute(
                    "INSERT INTO objects(id,kind,data) VALUES(?,'manifest',?)",
                    params![manifest.id, serde_json::to_string(&manifest)?],
                )?;
                tx.execute(
                    "INSERT INTO deps VALUES(?,?)",
                    params![manifest.id, manifest.dataset_ref],
                )?;
                remember(&tx, req, &refs)?;
                tx.commit()?;
            }
            open_response(&db, &manifest.id, &output.output)
        }
        "query" => {
            let p: QueryParams = decode(&req.params)?;
            crate::catalog::validate_label(p.label.as_deref())?;
            validate_provenance(p.provenance.as_ref())?;
            validate_execution(&p.execution)?;
            ensure!(
                p.execution.goal == "exact"
                    && matches!(p.execution.preview.as_str(), "available" | "none"),
                "UNSUPPORTED_OPERATION: SQL mode"
            );
            let prior = { existing(&db.conn.lock().unwrap(), req)? };
            let refs = if let Some(v) = prior {
                v
            } else {
                let (inputs, sources, quality) = resolve(&db, &p.bindings)?;
                submit(&db, req, Some(p.clone()), None, inputs, sources, quality)?
            };
            wait_response(&db, refs, p.execution.wait_ms, Some(p.execution.output)).await
        }
        "analyze" => {
            let p: AnalyzeParams = decode(&req.params)?;
            validate_execution(&p.execution)?;
            ensure!(
                p.execution.target_partitions.is_none_or(|n| n == 1),
                "UNSUPPORTED_OPERATION: progressive analysis processes frozen fragments sequentially"
            );
            ensure!(
                p.execution.goal == "exact"
                    && matches!(p.execution.preview.as_str(), "available" | "none"),
                "UNSUPPORTED_OPERATION: analysis mode"
            );
            let prior = { existing(&db.conn.lock().unwrap(), req)? };
            let refs = if let Some(v) = prior {
                v
            } else {
                let bindings =
                    BTreeMap::from([("source".into(), Binding::Dataset(p.source.clone()))]);
                let (inputs, sources, mut quality) = resolve(&db, &bindings)?;
                let input = inputs.values().next().unwrap();
                ensure!(
                    input.format == "parquet",
                    "UNSUPPORTED_OPERATION: analyze requires a frozen Parquet manifest"
                );
                let projection = crate::aggregate::projection(input, &p)?;
                quality["aggregation"] = json!({"aggregates":p.aggregates,"fragment_unit":p.fragment_unit,"average":"Decimal128(38,6); truncated toward zero at six fractional digits; exact sum and count retained in computation"});
                let query = QueryParams {
                    provenance: None,
                    label: None,
                    bindings,
                    sql: format!("SELECT {projection} FROM source"),
                    parameters: vec![],
                    execution: p.execution.clone(),
                    notify: Notify::default(),
                };
                submit(&db, req, Some(query), None, inputs, sources, quality)?
            };
            wait_response(&db, refs, p.execution.wait_ms, Some(p.execution.output)).await
        }
        "prepare" => {
            let p: PrepareParams = decode(&req.params)?;
            validate_execution(&p.execution)?;
            ensure!(
                p.execution.goal == "exact",
                "UNSUPPORTED_OPERATION: prepare requires exact execution"
            );
            let prior = { existing(&db.conn.lock().unwrap(), req)? };
            let refs = if let Some(v) = prior {
                v
            } else {
                let mut execution = p.execution.clone();
                execution.preview = "none".into();
                let query = QueryParams {
                    provenance: None,
                    label: None,
                    bindings: BTreeMap::from([("source".into(), Binding::Dataset(p.source))]),
                    sql: "SELECT * FROM source".into(),
                    parameters: vec![],
                    execution,
                    notify: Notify::default(),
                };
                let (inputs, sources, quality) = resolve(&db, &query.bindings)?;
                ensure!(
                    inputs
                        .values()
                        .all(|i| matches!(i.format.as_str(), "csv" | "tsv")),
                    "UNSUPPORTED_OPERATION: prepare accepts CSV/TSV manifests"
                );
                submit(&db, req, Some(query), None, inputs, sources, quality)?
            };
            wait_response(&db, refs, p.execution.wait_ms, None).await
        }
        "snapshot" => {
            let p: SnapshotParams = decode(&req.params)?;
            crate::catalog::validate_label(p.label.as_deref())?;
            validate_provenance(p.provenance.as_ref())?;
            validate_execution(&p.execution)?;
            ensure!(
                p.execution.goal == "exact",
                "UNSUPPORTED_OPERATION: snapshot requires exact execution"
            );
            let prior = { existing(&db.conn.lock().unwrap(), req)? };
            let refs = if let Some(v) = prior {
                v
            } else {
                if let Binding::Result(b) = &p.source {
                    let s = results::snapshot(&db, &b.result_ref, Some(b.revision))?;
                    ensure!(
                        s.job_state == "completed" && s.quality["final_for_request"] == true,
                        "RESULT_NOT_FINAL: snapshot requires a completed final revision"
                    );
                }
                let mut execution = p.execution.clone();
                execution.preview = "none".into();
                let query = QueryParams {
                    label: p.label,
                    provenance: p.provenance,
                    bindings: BTreeMap::from([("source".into(), p.source)]),
                    sql: "SELECT * FROM source".into(),
                    parameters: vec![],
                    execution,
                    notify: Notify::default(),
                };
                let (inputs, sources, quality) = resolve(&db, &query.bindings)?;
                ensure!(
                    quality["accuracy"] == "exact" && quality["coverage"]["kind"] == "complete",
                    "RESULT_NOT_FINAL: snapshot requires exact complete coverage"
                );
                submit(&db, req, Some(query), None, inputs, sources, quality)?
            };
            wait_response(&db, refs, p.execution.wait_ms, None).await
        }
        "read" => {
            let p: ReadParams = decode(&req.params)?;
            tokio::task::spawn_blocking(move || results::read(&db, &p)).await?
        }
        "inspect" => {
            let p: InspectParams = decode(&req.params)?;
            if p.checks
                .iter()
                .all(|c| matches!(c.as_str(), "schema" | "provenance"))
            {
                return inspect(&db, p);
            }
            let (query, inspection) = crate::profile::query(&db, &p)?;
            validate_execution(&query.execution)?;
            ensure!(
                query.execution.goal == "exact"
                    && matches!(query.execution.preview.as_str(), "available" | "none"),
                "UNSUPPORTED_OPERATION: inspection mode"
            );
            let wait = query.execution.wait_ms;
            let prior = { existing(&db.conn.lock().unwrap(), req)? };
            let refs = if let Some(v) = prior {
                v
            } else {
                let (inputs, sources, mut quality) = resolve(&db, &query.bindings)?;
                quality["inspection"] = inspection;
                submit(&db, req, Some(query), None, inputs, sources, quality)?
            };
            wait_response(&db, refs, wait, Some(p.budget)).await
        }
        "workspace" => {
            let p: WorkspaceParams = decode(&req.params)?;
            tokio::task::spawn_blocking(move || crate::storage::workspace(&db, p)).await?
        }
        "control" => {
            let p: ControlParams = decode(&req.params)?;
            match p.action.as_str() {
                "lookup" => {
                    let raw: Option<String> = db
                        .conn
                        .lock()
                        .unwrap()
                        .query_row(
                            "SELECT response FROM idempotency WHERE key=?",
                            [&p.object_ref],
                            |r| r.get(0),
                        )
                        .optional()?;
                    let refs: Value =
                        serde_json::from_str(&raw.ok_or_else(|| {
                            anyhow::anyhow!("OBJECT_NOT_FOUND: idempotency key")
                        })?)?;
                    let job = refs
                        .get("job_id")
                        .and_then(Value::as_str)
                        .map(|id| db.job(id))
                        .transpose()?;
                    Ok(json!({"accepted":true,"references":refs,"job":job}))
                }
                "pin" | "release" => crate::storage::retain(&db, &p.object_ref, p.action == "pin"),
                "status" => Ok(json!({"job":db.job(&p.object_ref)?})),
                "refresh" => {
                    let object = db.object(&p.object_ref)?;
                    ensure!(
                        object["managed"] != true,
                        "UNSUPPORTED_OPERATION: managed datasets are immutable; prepare a new source manifest"
                    );
                    let reference = object["manifest_ref"].as_str().ok_or_else(|| {
                        anyhow::anyhow!("INVALID_ARGUMENT: refresh requires a dataset")
                    })?;
                    let old: Manifest = serde_json::from_value(db.object(reference)?)?;
                    let options = OpenParams {
                        label: old.label.clone(),
                        source: old.source.to_string_lossy().into_owned(),
                        format: old.format,
                        header: old.header,
                        delimiter: Some((old.delimiter as char).to_string()),
                        schema: if old.schema_origin == "provided" {
                            Some(
                                old.schema
                                    .fields()
                                    .iter()
                                    .map(|f| Column {
                                        name: f.name().clone(),
                                        r#type: f.data_type().to_string(),
                                        nullable: f.is_nullable(),
                                    })
                                    .collect(),
                            )
                        } else {
                            None
                        },
                        infer_rows: 100,
                        output: OutputBudget::default(),
                    };
                    let mut refreshed =
                        tokio::task::spawn_blocking(move || crate::sources::open(&options))
                            .await??;
                    refreshed.dataset_ref = p.object_ref.clone();
                    {
                        let mut c = db.conn.lock().unwrap();
                        let tx = c.transaction()?;
                        tx.execute(
                            "INSERT INTO objects(id,kind,data) VALUES(?,'manifest',?)",
                            params![refreshed.id, serde_json::to_string(&refreshed)?],
                        )?;
                        tx.execute("UPDATE objects SET data=? WHERE id=?",params![json!({"dataset_ref":p.object_ref,"manifest_ref":refreshed.id,"source":refreshed.source,"label":refreshed.label}).to_string(),p.object_ref])?;
                        tx.execute(
                            "INSERT INTO deps VALUES(?,?)",
                            params![refreshed.id, p.object_ref],
                        )?;
                        tx.commit()?;
                    }
                    open_response(&db, &refreshed.id, &OutputBudget::default())
                }
                "wait" => {
                    ensure!(
                        p.wait_ms <= 30000,
                        "INVALID_ARGUMENT: wait_ms exceeds 30000"
                    );
                    wait_response(&db, json!({"job_id":p.object_ref}), p.wait_ms, None).await
                }
                "cancel" => {
                    let before = db.job(&p.object_ref)?;
                    db.cancel(&p.object_ref, "cancelled")?;
                    Ok(
                        json!({"already_terminal":terminal(before["state"].as_str().unwrap()),"job":db.job(&p.object_ref)?}),
                    )
                }
                "snapshot" => {
                    let mut snapshot = db.snapshot(&p.object_ref)?;
                    snapshot["event_cursor"] =
                        json!(hex::encode(serde_json::to_vec(&EventCursor {
                            store_id: db.store_id.clone(),
                            seq: snapshot["event_watermark"].as_str().unwrap().parse()?,
                            job_ids: vec![p.object_ref],
                            types: vec![],
                        })?));
                    Ok(snapshot)
                }
                _ => bail!("UNSUPPORTED_OPERATION: control action {}", p.action),
            }
        }
        "export" => {
            let p: ExportParams = decode(&req.params)?;
            ensure!(
                p.execution.target_partitions.is_none_or(|n| n == 1),
                "UNSUPPORTED_OPERATION: export writes fixed parts sequentially"
            );
            validate_execution(&p.execution)?;
            ensure!(
                matches!(p.format.as_str(), "csv" | "parquet" | "arrow"),
                "UNSUPPORTED_OPERATION: export format"
            );
            let prior = { existing(&db.conn.lock().unwrap(), req)? };
            let refs = if let Some(v) = prior {
                v
            } else {
                let snap = results::snapshot(&db, &p.result_ref, Some(p.revision))?;
                ensure!(snap.validity == "valid", "SOURCE_CHANGED");
                ensure!(
                    p.allow_nonfinal || snap.quality["final_for_request"] == true,
                    "INVALID_ARGUMENT: allow_nonfinal required"
                );
                ensure!(
                    p.allow_estimate || snap.quality["accuracy"] == "exact",
                    "INVALID_ARGUMENT: allow_estimate required"
                );
                let target = Path::new(&p.destination);
                ensure!(
                    target.is_absolute() && !target.exists(),
                    "INVALID_ARGUMENT: destination must be an absolute new path"
                );
                let mut bindings = BTreeMap::new();
                bindings.insert(
                    "input".into(),
                    Binding::Result(ResultBinding {
                        result_ref: p.result_ref.clone(),
                        revision: p.revision,
                    }),
                );
                let (inputs, sources, _) = resolve(&db, &bindings)?;
                submit(
                    &db,
                    req,
                    None,
                    Some(p.clone()),
                    inputs,
                    sources,
                    snap.quality,
                )?
            };
            wait_response(&db, refs, p.execution.wait_ms, None).await
        }
        "events" => events(&db, decode(&req.params)?).await,
        _ => bail!("UNSUPPORTED_OPERATION: method {}", req.method),
    }
}
fn open_response(db: &Db, manifest: &str, budget: &OutputBudget) -> Result<Value> {
    let raw = db.object(manifest)?;
    let m: Manifest = serde_json::from_value(raw.clone())?;
    let mut fields = vec![];
    let mut response = json!({"label":m.label,"binding":{"dataset_ref":m.dataset_ref,"manifest_ref":m.id},"dataset_ref":m.dataset_ref,"manifest_ref":m.id,"schema_ref":m.id,"format":m.format,"discovery":"complete","schema_origin":m.schema_origin,"schema_status":if m.schema_origin=="inferred"{"inferred_unvalidated_tail"}else{"declared"},"field_count":m.schema.fields().len(),"fields":[],"source_consistency":"best_effort","validity":raw["validity"],"metadata_read_bytes":m.metadata_bytes,"inferred_rows":m.inferred_rows,"file_count":m.files.len(),"rows":m.files.iter().map(|f|f.rows).collect::<Option<Vec<_>>>().map(|v|v.iter().sum::<u64>()),"next_field_offset":null});
    ensure!(
        budget.max_bytes <= FRAME_LIMIT - 512 && budget.max_rows <= 10000,
        "INVALID_ARGUMENT: output budget"
    );
    ensure!(
        serde_json::to_vec(&response)?.len() + 256 < budget.max_bytes,
        "OUTPUT_BUDGET_TOO_SMALL"
    );
    for field in m.schema.fields().iter().take(budget.max_rows) {
        fields.push(json!({"name":field.name(),"type":field.data_type().to_string(),"nullable":field.is_nullable()}));
        response["fields"] = json!(fields);
        if serde_json::to_vec(&response)?.len() + 256 > budget.max_bytes {
            fields.pop();
            break;
        }
    }
    response["fields"] = json!(fields);
    response["next_field_offset"] = if fields.len() < m.schema.fields().len() {
        json!(fields.len())
    } else {
        Value::Null
    };
    Ok(response)
}
fn inspect(db: &Db, p: InspectParams) -> Result<Value> {
    ensure!(
        p.checks
            .iter()
            .all(|s| matches!(s.as_str(), "schema" | "provenance")),
        "UNSUPPORTED_OPERATION: inspect currently supports schema; use bounded SQL for rows/statistics"
    );
    let (schema, mut out) = if p.object_ref.starts_with("res_") {
        let snapshot = results::snapshot(db, &p.object_ref, p.revision)?;
        let scope: String = db.conn.lock().unwrap().query_row(
            "SELECT j.scope_ref FROM results r JOIN jobs j ON j.id=r.job_id WHERE r.id=?",
            [&p.object_ref],
            |r| r.get(0),
        )?;
        let out = json!({"ref":p.object_ref,"revision":snapshot.revision,"scope_ref":scope,"schema_origin":"materialized_result","quality":snapshot.quality,"validity":snapshot.validity,"row_count":snapshot.rows.to_string(),"verification":{"validity":"stored","original_sources":"not_rechecked","parts":"checked_when_read_or_used"}});
        (snapshot.schema, out)
    } else {
        let mut v = db.object(&p.object_ref)?;
        if let Some(manifest) = v.get("manifest_ref").and_then(Value::as_str) {
            v = db.object(manifest)?;
        }
        if p.object_ref.starts_with("scope_") || p.object_ref.starts_with("vw_") {
            ensure!(
                serde_json::to_vec(&v)?.len() + 256 <= p.budget.max_bytes,
                "OUTPUT_BUDGET_TOO_SMALL"
            );
            return Ok(v);
        }
        let validity = v["validity"].clone();
        let m: Manifest = serde_json::from_value(v)?;
        let mut out = json!({"ref":p.object_ref,"manifest_ref":m.id,"schema_origin":m.schema_origin,"validity":validity});
        if p.checks.iter().any(|c| c == "provenance") {
            let dataset = db.object(&m.dataset_ref)?;
            out["scope_ref"] = dataset["scope_ref"].clone();
            out["identity"] = json!({"dataset_ref":m.dataset_ref,"manifest_ref":m.id,"source":m.source,"format":m.format,"files":m.files,"header":m.header,"delimiter":m.delimiter});
            out["provenance"] = m
                .schema
                .metadata()
                .get("rowtrail.provenance")
                .map(|s| serde_json::from_str::<Value>(s))
                .transpose()?
                .unwrap_or(Value::Null);
            out["verification"] = json!({"validity":"stored","files":"not_rechecked","independent":m.schema_origin=="prepared"});
        }
        (m.schema, out)
    };
    let fields=schema.fields().iter().enumerate().filter(|(i,f)|*i>=p.offset&&(p.columns.is_empty()||p.columns.contains(f.name()))).map(|(i,f)|(i,json!({"name":f.name(),"type":f.data_type().to_string(),"nullable":f.is_nullable()}))).collect::<Vec<_>>();
    out["field_count"] = json!(schema.fields().len());
    out["fields"] = json!([]);
    out["next_field_offset"] = Value::Null;
    let mut next = None;
    for (i, field) in fields {
        if out["fields"].as_array().unwrap().len() >= p.budget.max_rows {
            next = Some(i);
            break;
        }
        out["fields"].as_array_mut().unwrap().push(field);
        if serde_json::to_vec(&out)?.len() + 256 > p.budget.max_bytes {
            out["fields"].as_array_mut().unwrap().pop();
            next = Some(i);
            break;
        }
    }
    out["next_field_offset"] = json!(next);
    ensure!(
        serde_json::to_vec(&out)?.len() + 256 <= p.budget.max_bytes,
        "OUTPUT_BUDGET_TOO_SMALL"
    );
    Ok(out)
}
fn resolve(
    db: &Db,
    bindings: &BTreeMap<String, Binding>,
) -> Result<(BTreeMap<String, Input>, Vec<Manifest>, Value)> {
    let _guard = db.gc.read().unwrap();
    let mut inputs = BTreeMap::new();
    let mut sources = vec![];
    let mut partial = false;
    let mut estimate = false;
    let mut lineage = vec![];
    for (alias, binding) in bindings {
        ensure!(
            !alias.is_empty()
                && alias.len() <= 128
                && alias
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_'),
            "INVALID_ARGUMENT: binding alias"
        );
        let input = match binding {
            Binding::Dataset(b) => {
                let raw = db.object(&b.manifest_ref)?;
                ensure!(
                    raw["validity"] == "valid",
                    "SOURCE_CHANGED: invalid manifest"
                );
                let m: Manifest = serde_json::from_value(raw)?;
                ensure!(
                    m.dataset_ref == b.dataset_ref,
                    "INVALID_ARGUMENT: dataset/manifest mismatch"
                );
                if let Err(e) = m.validate() {
                    db.invalidate(&m.id)?;
                    return Err(e);
                }
                let input = Input {
                    format: m.format.clone(),
                    schema: m.schema.clone(),
                    files: m.files.clone(),
                    header: m.header,
                    delimiter: m.delimiter,
                    source_ref: m.id.clone(),
                    quality: m
                        .schema
                        .metadata()
                        .get("rowtrail.quality")
                        .map(|s| serde_json::from_str(s))
                        .transpose()?
                        .unwrap_or_else(
                            || json!({"accuracy":"exact","coverage":{"kind":"complete"}}),
                        ),
                };
                sources.push(m);
                input
            }
            Binding::Result(b) => {
                let s = results::snapshot(db, &b.result_ref, Some(b.revision))?;
                ensure!(s.validity == "valid", "SOURCE_CHANGED: invalid result");
                partial |= s.quality["coverage"]["kind"] != "complete";
                estimate |= s.quality["accuracy"] != "exact";
                let files = s
                    .parts
                    .iter()
                    .map(|(_p, _size, hash, _rows, identity)| {
                        identity
                            .validate()
                            .map_err(|e| anyhow::anyhow!("RESULT_CORRUPT: {e}"))?;
                        let mut file = identity.clone();
                        file.checksum = Some(hash.clone());
                        Ok(file)
                    })
                    .collect::<Result<Vec<_>>>()?;
                Input {
                    format: "arrow".into(),
                    schema: s.schema,
                    files,
                    header: false,
                    delimiter: b',',
                    source_ref: b.result_ref.clone(),
                    quality: s.quality,
                }
            }
        };
        partial |= input.quality["coverage"]["kind"] != "complete";
        estimate |= input.quality["accuracy"] != "exact";
        lineage.push(json!({"binding":binding,"accuracy":input.quality["accuracy"],"coverage":input.quality["coverage"],"numeric":input.quality.get("numeric").cloned().unwrap_or(json!({"policy":"unknown"}))}));
        inputs.insert(alias.clone(), input);
    }
    let quality = json!({"accuracy":if estimate{"estimate"}else{"exact"},"coverage":{"kind":if partial{"partial"}else{"complete"}},"final_for_request":false,"source_consistency":if sources.is_empty(){"immutable_materialized"}else{"best_effort"},"numeric":{"policy":"rowtrail-numeric-v1","other_sql":"engine_semantics","input_provenance":if lineage.iter().all(|v|v["numeric"]["policy"]=="rowtrail-numeric-v1" && v["numeric"]["input_provenance"]=="declared"){"declared"}else{"unknown"}},"lineage":lineage});
    Ok((inputs, sources, quality))
}
fn submit(
    db: &Db,
    req: &Request,
    query: Option<QueryParams>,
    export: Option<ExportParams>,
    inputs: BTreeMap<String, Input>,
    sources: Vec<Manifest>,
    mut quality: Value,
) -> Result<Value> {
    let _guard = db.gc.read().unwrap();
    let job = id("job");
    let result = id("res");
    let scope = id("scope");
    let view = id("vw");
    if query.is_some() {
        quality["coverage"]["scope_ref"] = json!(scope);
    }
    let spec = JobSpec {
        job_id: job.clone(),
        attempt: id("attempt"),
        result_ref: result.clone(),
        scope_ref: scope.clone(),
        workspace: db.workspace.clone(),
        query,
        export,
        analysis: if req.method == "analyze" {
            Some(decode(&req.params)?)
        } else {
            None
        },
        prepared: matches!(req.method.as_str(), "prepare" | "snapshot").then(|| DatasetBinding {
            dataset_ref: id("ds"),
            manifest_ref: id("mf"),
        }),
        inputs,
        sources,
        quality: quality.clone(),
    };
    let spec_json = serde_json::to_string(&spec)?;
    ensure!(
        spec_json.len() <= FRAME_LIMIT,
        "RESOURCE_EXHAUSTED: job specification exceeds IPC frame"
    );
    let refs = if let Some(p) = &spec.prepared {
        json!({"job_id":job,"dataset_ref":p.dataset_ref,"manifest_ref":p.manifest_ref,"scope_ref":scope})
    } else {
        json!({"job_id":job,"result_ref":result,"view_ref":view,"scope_ref":scope})
    };
    let mut c = db.conn.lock().unwrap();
    let tx = c.transaction()?;
    if let Some(v) = existing(&tx, req)? {
        return Ok(v);
    }
    crate::storage::admit(&tx, spec.query.as_ref())?;
    for input in spec.inputs.values() {
        let validity: String = tx.query_row("SELECT validity FROM objects WHERE id=?1 UNION ALL SELECT validity FROM results WHERE id=?1", [&input.source_ref], |r| r.get(0))?;
        ensure!(
            validity != "expired",
            "OBJECT_EXPIRED: {}",
            input.source_ref
        );
        ensure!(validity == "valid", "SOURCE_CHANGED: invalid input");
    }
    tx.execute("INSERT INTO jobs(id,state,phase,spec,attempt,result_ref,scope_ref,created) VALUES(?,'queued','queued',?,?,?,?,?)",params![job,spec_json,spec.attempt,result,scope,now_ms()])?;
    tx.execute(
        "INSERT INTO results(id,job_id,quality) VALUES(?,?,?)",
        params![result, job, quality.to_string()],
    )?;
    if let Some(label) = spec.query.as_ref().and_then(|q| q.label.as_ref()) {
        tx.execute(
            "INSERT INTO catalog_labels VALUES(?1,?3),(?2,?3)",
            params![job, result, label],
        )?;
    }
    tx.execute(
        "INSERT INTO objects(id,kind,data) VALUES(?,'view',?)",
        params![
            view,
            json!({"view_ref":view,"request":req.params}).to_string()
        ],
    )?;
    tx.execute("INSERT INTO objects(id,kind,data) VALUES(?,'scope',?)",params![scope,json!({"scope_ref":scope,"provenance":spec.query.as_ref().and_then(|q|q.provenance.as_ref()),"label":spec.query.as_ref().and_then(|q|q.label.as_ref()),"inputs":spec.query.as_ref().map(|q|&q.bindings),"sql":spec.query.as_ref().map(|q|&q.sql),"parameters":spec.query.as_ref().map(|q|&q.parameters),"coverage_semantics":if spec.analysis.is_some(){"one cumulative aggregate checkpoint over complete fragments in frozen manifest order"}else{"committed output prefix until final; input scan coverage unknown"},"quality":quality}).to_string()])?;
    for input in spec.inputs.values() {
        tx.execute(
            "INSERT OR IGNORE INTO deps VALUES(?,?)",
            params![result, input.source_ref],
        )?;
    }
    event(
        &tx,
        &job,
        "job.accepted",
        json!({"state":"queued","result_ref":result}),
    )?;
    remember(&tx, req, &refs)?;
    tx.commit()?;
    db.work_available.notify_one();
    db.changed.send_replace(());
    Ok(refs)
}
async fn wait_response(
    db: &Db,
    mut refs: Value,
    wait: u64,
    output: Option<OutputBudget>,
) -> Result<Value> {
    let job = refs["job_id"].as_str().unwrap().to_owned();
    let until = tokio::time::Instant::now() + std::time::Duration::from_millis(wait);
    let mut changed = db.changed.subscribe();
    let state = loop {
        let state = db.job(&job)?;
        if terminal(state["state"].as_str().unwrap()) || tokio::time::Instant::now() >= until {
            break state;
        }
        let _ = tokio::time::timeout_at(until, changed.changed()).await;
    };
    refs["job"] = state.clone();
    refs["readable_revision"] = state["readable_revision"].clone();
    refs["observation"] = Value::Null;
    let prepared = state["prepared"].is_object();
    refs["binding"] = if prepared {
        if state["state"] == "completed" {
            state["prepared"].clone()
        } else {
            Value::Null
        }
    } else if let Some(revision) = state["readable_revision"].as_u64() {
        json!({"result_ref":state["result_ref"],"revision":revision})
    } else {
        Value::Null
    };
    if let (Some(budget), Some(revision)) = (output, state["readable_revision"].as_u64()) {
        // read reserves its own response-envelope headroom. Reserve the actual
        // surrounding job metadata too, so a large page fits without a second
        // read. Tight budgets spend bytes on the answer before optional metrics.
        if budget.max_bytes < 4096 {
            refs["job"]["metrics"] = Value::Null;
        }
        let overhead = serde_json::to_vec(&refs)?.len() + 32;
        let p = ReadParams {
            result_ref: state["result_ref"].as_str().unwrap().into(),
            revision: Some(revision),
            cursor: None,
            columns: vec![],
            max_rows: budget.max_rows,
            max_bytes: budget.max_bytes.saturating_sub(overhead),
        };
        match results::read(db, &p) {
            Ok(observation) => refs["observation"] = observation,
            Err(error) => {
                let code = crate::errors::code(&error, "INTERNAL_ERROR");
                if code == "OUTPUT_BUDGET_TOO_SMALL" {
                    refs["observation_omitted"] = json!("output_budget");
                } else {
                    refs["observation_error"] = json!({"code":code,"message":error.to_string()});
                }
            }
        }
    }
    let mut actions = vec![];
    if prepared && state["state"] == "completed" {
        actions.extend(["inspect", "query"]);
    } else if !prepared && state["readable_revision"].is_number() {
        if refs["observation"].is_null() || refs["observation"]["presentation"]["has_more"] == true
        {
            actions.push("read");
        }
        actions.push("query");
    }
    if !terminal(state["state"].as_str().unwrap()) {
        actions.extend(["wait", "cancel"]);
    }
    refs["next_actions"] = json!(actions);
    Ok(refs)
}
#[derive(serde::Serialize, serde::Deserialize)]
struct EventCursor {
    store_id: String,
    seq: u64,
    job_ids: Vec<String>,
    types: Vec<String>,
}
async fn events(db: &Db, p: EventsParams) -> Result<Value> {
    ensure!(
        p.wait_ms <= 30000 && p.limit > 0 && p.limit <= 1000 && p.max_bytes <= FRAME_LIMIT - 512,
        "INVALID_ARGUMENT: event limits"
    );
    let start = if let Some(raw) = &p.after_cursor {
        let c: EventCursor = serde_json::from_slice(&hex::decode(raw)?)?;
        ensure!(
            c.store_id == db.store_id && c.job_ids == p.job_ids && c.types == p.types,
            "INVALID_CURSOR: event filter/workspace mismatch"
        );
        c.seq
    } else {
        0
    };
    let deadline = tokio::time::Instant::now() + std::time::Duration::from_millis(p.wait_ms);
    let mut changed = db.changed.subscribe();
    loop {
        let mut last = start;
        let mut list = vec![];
        {
            let c = db.conn.lock().unwrap();
            let mut stmt = c.prepare(
                "SELECT seq,job_id,kind,payload FROM events WHERE seq>? ORDER BY seq LIMIT 1000",
            )?;
            let rows = stmt.query_map([start], |r| {
                Ok((
                    r.get::<_, u64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                ))
            })?;
            for row in rows {
                let (seq, job, kind, value) = row?;
                if (!p.job_ids.is_empty() && !p.job_ids.contains(&job))
                    || (!p.types.is_empty() && !p.types.contains(&kind))
                {
                    last = seq;
                    continue;
                }
                let mut value: Value = serde_json::from_str(&value)?;
                value["seq"] = json!(seq.to_string());
                list.push(value);
                if list.len() > p.limit || serde_json::to_vec(&list)?.len() + 1024 > p.max_bytes {
                    list.pop();
                    ensure!(
                        !list.is_empty(),
                        "OUTPUT_BUDGET_TOO_SMALL: event does not fit"
                    );
                    break;
                }
                last = seq;
            }
        }
        if !list.is_empty() || last > start || tokio::time::Instant::now() >= deadline {
            let cursor = hex::encode(serde_json::to_vec(&EventCursor {
                store_id: db.store_id.clone(),
                seq: last,
                job_ids: p.job_ids,
                types: p.types,
            })?);
            return Ok(json!({"events":list,"next_cursor":cursor}));
        }
        let _ = tokio::time::timeout_at(deadline, changed.changed()).await;
    }
}
