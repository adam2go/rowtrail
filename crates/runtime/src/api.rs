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
    let mut p = req.params.clone();
    if let Some(o) = p.as_object_mut() {
        o.remove("output");
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
    json!({"api_version":"1","formats":["csv","tsv","parquet"],"source":"local","sql":"DataFusion 55.0.0 read-only SELECT; explicit bindings","analysis":[],"entries":["cli","mcp_stdio","rust_sdk"],"native_tasks":false,"automatic_resume":false,"source_consistency":"best_effort","result_storage":"immutable Arrow IPC files","max_frame_bytes":FRAME_LIMIT,"minimum_error_budget_bytes":512,"max_output_bytes":FRAME_LIMIT-512,"max_output_rows":10000,"max_manifest_files":128,"max_directory_entries":4096,"default_parallel_jobs":1,"cancellation":"cooperative signal with forced worker termination after 300ms","engine_memory_limit":"MemoryPool; not RSS hard limit","scan_limit":"byte reservation before local reads","result_retention":"until workspace removal; release and automatic GC not implemented","event_retention":"until workspace removal; no event pruning yet","unsupported":["Windows","remote","sampling","native_tasks","automatic_resume","union_by_name","prepare","binary_inline","RSS_hard_limit"]})
}
fn validate_execution(e: &Execution) -> Result<()> {
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

fn output_limit(req: &Request) -> usize {
    ((match req.method.as_str() {
        "query" => req
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
        "events" => req
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
            if !fits(&v) && v.get("observation").is_some() {
                v["observation"] = Value::Null;
                v["observation_omitted"] = json!("output_budget");
            }
            if !fits(&v) && v.get("job").is_some() {
                v["job"]["metrics"] = Value::Null;
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
            Response::failure(&req, ApiError::new(code, message))
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
                tx.execute("INSERT INTO objects(id,kind,data) VALUES(?,'dataset',?)",params![manifest.dataset_ref,json!({"dataset_ref":manifest.dataset_ref,"manifest_ref":manifest.id,"source":manifest.source}).to_string()])?;
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
        "read" => {
            let p: ReadParams = decode(&req.params)?;
            tokio::task::spawn_blocking(move || results::read(&db, &p)).await?
        }
        "inspect" => {
            let p: InspectParams = decode(&req.params)?;
            if !p.checks.iter().any(|c| c == "head") {
                return inspect(&db, p);
            }
            ensure!(
                p.checks.iter().all(|c| c == "schema" || c == "head"),
                "UNSUPPORTED_OPERATION: inspect checks"
            );
            ensure!(
                p.budget.max_rows > 0,
                "INVALID_ARGUMENT: head requires positive max_rows"
            );
            let mut object = db.object(&p.object_ref)?;
            if let Some(reference) = object.get("manifest_ref").and_then(Value::as_str) {
                object = db.object(reference)?;
            }
            let m: Manifest = serde_json::from_value(object)?;
            let columns = if p.columns.is_empty() {
                m.schema
                    .fields()
                    .iter()
                    .map(|f| f.name().clone())
                    .collect::<Vec<_>>()
            } else {
                p.columns.clone()
            };
            for col in &columns {
                m.schema.index_of(col)?;
            }
            let projection = columns
                .iter()
                .map(|s| format!("\"{}\"", s.replace('"', "\"\"")))
                .collect::<Vec<_>>()
                .join(",");
            let mut bindings = BTreeMap::new();
            bindings.insert(
                "source".into(),
                Binding::Dataset(DatasetBinding {
                    dataset_ref: m.dataset_ref,
                    manifest_ref: m.id,
                }),
            );
            let query = QueryParams {
                bindings,
                sql: format!(
                    "SELECT {projection} FROM source LIMIT {}",
                    p.budget.max_rows
                ),
                parameters: vec![],
                execution: Execution {
                    scan_bytes: 1024 * 1024,
                    run_timeout_ms: 1000,
                    output: p.budget.clone(),
                    ..Default::default()
                },
                notify: Notify::default(),
            };
            validate_execution(&query.execution)?;
            let prior = { existing(&db.conn.lock().unwrap(), req)? };
            let refs = if let Some(v) = prior {
                v
            } else {
                let (inputs, sources, quality) = resolve(&db, &query.bindings)?;
                submit(&db, req, Some(query), None, inputs, sources, quality)?
            };
            wait_response(&db, refs, 200, Some(p.budget)).await
        }
        "control" => {
            let p: ControlParams = decode(&req.params)?;
            match p.action.as_str() {
                "status" => Ok(json!({"job":db.job(&p.object_ref)?})),
                "refresh" => {
                    let object = db.object(&p.object_ref)?;
                    let reference = object["manifest_ref"].as_str().ok_or_else(|| {
                        anyhow::anyhow!("INVALID_ARGUMENT: refresh requires a dataset")
                    })?;
                    let old: Manifest = serde_json::from_value(db.object(reference)?)?;
                    let options = OpenParams {
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
                        tx.execute("UPDATE objects SET data=? WHERE id=?",params![json!({"dataset_ref":p.object_ref,"manifest_ref":refreshed.id,"source":refreshed.source}).to_string(),p.object_ref])?;
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
    let mut response = json!({"dataset_ref":m.dataset_ref,"manifest_ref":m.id,"schema_ref":m.id,"format":m.format,"discovery":"complete","schema_origin":m.schema_origin,"schema_status":if m.schema_origin=="inferred"{"inferred_unvalidated_tail"}else{"declared"},"field_count":m.schema.fields().len(),"fields":[],"source_consistency":"best_effort","validity":raw["validity"],"metadata_read_bytes":m.metadata_bytes,"inferred_rows":m.inferred_rows,"file_count":m.files.len(),"rows":m.files.iter().map(|f|f.rows).collect::<Option<Vec<_>>>().map(|v|v.iter().sum::<u64>()),"next_field_offset":null});
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
        p.checks.iter().all(|s| s == "schema"),
        "UNSUPPORTED_OPERATION: inspect currently supports schema; use bounded SQL for rows/statistics"
    );
    let (schema, mut out) = if p.object_ref.starts_with("res_") {
        let snapshot = results::snapshot(db, &p.object_ref, None)?;
        let out = json!({"ref":p.object_ref,"revision":snapshot.revision,"schema_origin":"materialized_result","quality":snapshot.quality,"validity":snapshot.validity,"row_count":snapshot.rows.to_string()});
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
        let m: Manifest = serde_json::from_value(v)?;
        let out = json!({"ref":p.object_ref,"manifest_ref":m.id,"schema_origin":m.schema_origin});
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
                    .map(|(_p, _size, _hash, _rows, identity)| {
                        identity
                            .validate()
                            .map_err(|e| anyhow::anyhow!("RESULT_CORRUPT: {e}"))?;
                        Ok(identity.clone())
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
        lineage.push(json!({"binding":binding,"accuracy":input.quality["accuracy"],"coverage":input.quality["coverage"]}));
        inputs.insert(alias.clone(), input);
    }
    let quality = json!({"accuracy":if estimate{"estimate"}else{"exact"},"coverage":{"kind":if partial{"partial"}else{"complete"}},"final_for_request":false,"source_consistency":if sources.is_empty(){"immutable_materialized"}else{"best_effort"},"lineage":lineage});
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
        inputs,
        sources,
        quality: quality.clone(),
    };
    let spec_json = serde_json::to_string(&spec)?;
    ensure!(
        spec_json.len() <= FRAME_LIMIT,
        "RESOURCE_EXHAUSTED: job specification exceeds IPC frame"
    );
    let refs = json!({"job_id":job,"result_ref":result,"view_ref":view,"scope_ref":scope});
    let mut c = db.conn.lock().unwrap();
    let tx = c.transaction()?;
    if let Some(v) = existing(&tx, req)? {
        return Ok(v);
    }
    tx.execute("INSERT INTO jobs(id,state,phase,spec,attempt,result_ref,scope_ref,created) VALUES(?,'queued','queued',?,?,?,?,?)",params![job,spec_json,spec.attempt,result,scope,now_ms()])?;
    tx.execute(
        "INSERT INTO results(id,job_id,quality) VALUES(?,?,?)",
        params![result, job, quality.to_string()],
    )?;
    tx.execute(
        "INSERT INTO objects(id,kind,data) VALUES(?,'view',?)",
        params![
            view,
            json!({"view_ref":view,"request":req.params}).to_string()
        ],
    )?;
    tx.execute("INSERT INTO objects(id,kind,data) VALUES(?,'scope',?)",params![scope,json!({"scope_ref":scope,"inputs":spec.query.as_ref().map(|q|&q.bindings),"sql":spec.query.as_ref().map(|q|&q.sql),"parameters":spec.query.as_ref().map(|q|&q.parameters),"coverage_semantics":"committed output prefix until final; input scan coverage unknown","quality":quality}).to_string()])?;
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
    let state = loop {
        let state = db.job(&job)?;
        if terminal(state["state"].as_str().unwrap()) || tokio::time::Instant::now() >= until {
            break state;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    };
    refs["job"] = state.clone();
    refs["readable_revision"] = state["readable_revision"].clone();
    refs["observation"] = Value::Null;
    if let (Some(budget), Some(revision)) = (output, state["readable_revision"].as_u64()) {
        let p = ReadParams {
            result_ref: state["result_ref"].as_str().unwrap().into(),
            revision: Some(revision),
            cursor: None,
            columns: vec![],
            max_rows: budget.max_rows,
            max_bytes: budget.max_bytes.saturating_sub(1024),
        };
        if let Ok(observation) = results::read(db, &p) {
            refs["observation"] = observation;
        }
    }
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
        tokio::time::sleep(std::time::Duration::from_millis(25)).await;
    }
}
