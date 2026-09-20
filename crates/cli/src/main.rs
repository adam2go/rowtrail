use anyhow::{Result, ensure};
use clap::{Parser, Subcommand};
use rmcp::{ErrorData, RoleServer, ServerHandler, ServiceExt, model::*, service::RequestContext};
use rowtrail_client::Client;
use rowtrail_contracts::{
    ApiError, Binding, DatasetBinding, Execution, Notify, QueryParams, Request, Response,
    ResultBinding,
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    io::Read,
    path::{Path, PathBuf},
};

#[derive(Parser)]
#[command(version, about = "Persistent, bounded data exploration for agents")]
struct Args {
    #[arg(long, global = true)]
    workspace: Option<PathBuf>,
    #[arg(long, global = true)]
    json: bool,
    #[arg(long, global = true)]
    idempotency_key: Option<String>,
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    Schema {
        #[arg(default_value = "query")]
        method: String,
    },
    Mcp,
    /// Exchange full request/response envelopes as NDJSON on one connection.
    Session,
    Doctor,
    /// Send a contract request from a JSON file or stdin (-).
    Call {
        method: String,
        #[arg(long, default_value = "-")]
        request: PathBuf,
    },
    Open {
        source: PathBuf,
        #[arg(long, default_value = "auto")]
        format: String,
        #[arg(long)]
        schema: Option<PathBuf>,
        #[arg(long)]
        delimiter: Option<String>,
        #[arg(long)]
        no_header: bool,
    },
    Inspect {
        reference: String,
        #[arg(long)]
        revision: Option<u64>,
        #[arg(long, default_value_t = 10)]
        top_k: usize,
        #[arg(long, value_delimiter = ',')]
        columns: Vec<String>,
        #[arg(long, value_delimiter = ',', default_value = "schema")]
        checks: Vec<String>,
        #[arg(long, default_value_t = 0)]
        offset: usize,
        #[arg(long, default_value_t = 100)]
        max_rows: usize,
        #[arg(long, default_value_t = 8192)]
        max_bytes: usize,
    },
    /// Stream a frozen CSV/TSV manifest to a managed Parquet dataset.
    Prepare {
        dataset: String,
        #[arg(long)]
        manifest: String,
        #[arg(long, default_value_t = 200)]
        wait_ms: u64,
    },
    /// Inspect usage, configure a managed-data quota, or collect released data.
    Workspace {
        #[arg(default_value = "usage")]
        action: String,
        #[arg(long)]
        quota_bytes: Option<u64>,
        #[arg(long)]
        apply: bool,
    },
    Pin {
        reference: String,
    },
    Release {
        reference: String,
    },
    Query {
        #[arg(long)]
        request: Option<PathBuf>,
        #[arg(long, conflicts_with = "request")]
        sql: Option<String>,
        #[arg(long)]
        bind: Vec<String>,
        #[arg(long)]
        r#async: bool,
        #[arg(long)]
        wait_ms: Option<u64>,
    },
    Read {
        result: String,
        #[arg(long)]
        revision: Option<u64>,
        #[arg(long)]
        cursor: Option<String>,
        #[arg(long, value_delimiter = ',')]
        columns: Vec<String>,
        #[arg(long, default_value_t = 100)]
        max_rows: usize,
        #[arg(long, default_value_t = 8192)]
        max_bytes: usize,
    },
    Job {
        #[command(subcommand)]
        action: JobCommand,
    },
    Export {
        result: String,
        #[arg(long)]
        revision: u64,
        #[arg(long, default_value = "parquet")]
        format: String,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        allow_nonfinal: bool,
        #[arg(long)]
        allow_estimate: bool,
    },
    Events {
        #[arg(long)]
        after: Option<String>,
        #[arg(long)]
        job: Vec<String>,
        #[arg(long, value_delimiter = ',')]
        types: Vec<String>,
        #[arg(long)]
        follow: bool,
        #[arg(long)]
        jsonl: bool,
    },
}
#[derive(Subcommand)]
enum JobCommand {
    Get {
        job: String,
    },
    Wait {
        job: String,
        #[arg(long, default_value_t = 1000)]
        wait_ms: u64,
    },
    Cancel {
        job: String,
    },
}

fn load(path: &Path) -> Result<Value> {
    let mut bytes = vec![];
    if path == Path::new("-") {
        std::io::stdin()
            .take(rowtrail_contracts::FRAME_LIMIT as u64 + 1)
            .read_to_end(&mut bytes)?;
    } else {
        bytes = std::fs::read(path)?;
    }
    ensure!(
        bytes.len() <= rowtrail_contracts::FRAME_LIMIT,
        "request exceeds frame limit"
    );
    Ok(serde_json::from_slice(&bytes)?)
}
fn absolute(path: &Path) -> Result<PathBuf> {
    if path.is_absolute() {
        Ok(path.to_owned())
    } else {
        Ok(std::env::current_dir()?.join(path))
    }
}
fn parse_bindings(bind: Vec<String>) -> Result<BTreeMap<String, Binding>> {
    let mut map = BTreeMap::new();
    for s in bind {
        let (alias, input) = s.split_once('=').ok_or_else(|| {
            anyhow::anyhow!("binding must be alias=ds_ID@mf_ID or alias=res_ID@revision")
        })?;
        let (reference, version) = input
            .split_once('@')
            .ok_or_else(|| anyhow::anyhow!("bindings require a fixed manifest or revision"))?;
        let binding = if reference.starts_with("ds_") {
            Binding::Dataset(DatasetBinding {
                dataset_ref: reference.into(),
                manifest_ref: version.into(),
            })
        } else {
            Binding::Result(ResultBinding {
                result_ref: reference.into(),
                revision: version.parse()?,
            })
        };
        ensure!(
            map.insert(alias.into(), binding).is_none(),
            "duplicate binding"
        );
    }
    Ok(map)
}
fn request_from(method: &str, value: Value) -> Result<Request> {
    if value.get("api_version").is_some() {
        let req: Request = serde_json::from_value(value)?;
        ensure!(
            req.method == method,
            "request method does not match command"
        );
        Ok(req)
    } else {
        Ok(Request::new(method, value))
    }
}
fn exit_code(response: &Response, wait: bool) -> i32 {
    if !response.ok {
        return match response.error.as_ref().map(|e| e.code.as_str()) {
            Some(
                "INVALID_ARGUMENT"
                | "UNSUPPORTED_OPERATION"
                | "OUTPUT_BUDGET_TOO_SMALL"
                | "INVALID_CURSOR",
            ) => 2,
            _ => 5,
        };
    }
    if wait {
        let state = response
            .result
            .as_ref()
            .and_then(|v| v["job"]["state"].as_str())
            .unwrap_or("");
        return if state == "completed" {
            0
        } else if rowtrail_contracts::terminal(state) {
            4
        } else {
            3
        };
    }
    0
}
#[derive(Clone)]
struct Mcp {
    workspace: PathBuf,
}
fn tool(name: &str) -> Option<Tool> {
    let method = name.strip_prefix("data_")?;
    let mut schema = rowtrail_contracts::schema(method)?;
    schema["properties"]["_request"] = json!({"type":"object","additionalProperties":false,"properties":{"request_id":{"type":"string"},"idempotency_key":{"type":"string"}}});
    Some(Tool::new(
        name.to_owned(),
        format!(
            "RowTrail {method}. Uses persistent jobs and fixed result revisions. Inspect returned job state and quality; accepted does not mean completed."
        ),
        schema.as_object()?.clone(),
    ))
}
impl ServerHandler for Mcp {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build()).with_server_info(Implementation::new("rowtrail",env!("CARGO_PKG_VERSION"))).with_instructions("Open local data, inspect schema, query explicit fixed inputs, and read/export bounded results. Long jobs continue after calls return. Use data_control/status/wait/cancel and data_events. No automatic host resume or native Tasks is advertised.")
    }
    fn get_tool(&self, name: &str) -> Option<Tool> {
        tool(name)
    }
    async fn list_tools(
        &self,
        _: Option<PaginatedRequestParams>,
        _: RequestContext<RoleServer>,
    ) -> std::result::Result<ListToolsResult, ErrorData> {
        Ok(ListToolsResult {
            tools: [
                "open",
                "inspect",
                "prepare",
                "query",
                "read",
                "control",
                "export",
                "events",
                "workspace",
            ]
            .into_iter()
            .map(|n| tool(&format!("data_{n}")).unwrap())
            .collect(),
            ..Default::default()
        })
    }
    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _: RequestContext<RoleServer>,
    ) -> std::result::Result<CallToolResponse, ErrorData> {
        let method = request.name.strip_prefix("data_").unwrap_or("");
        let result: Result<Response> = async {
            ensure!(
                rowtrail_contracts::schema(method).is_some(),
                "UNSUPPORTED_OPERATION: unknown tool"
            );
            let mut params = Value::Object(request.arguments.unwrap_or_default());
            let options = params
                .as_object_mut()
                .unwrap()
                .remove("_request")
                .unwrap_or(Value::Null);
            let mut req = Request::new(method, params);
            if let Some(id) = options["request_id"].as_str() {
                req.request_id = id.into()
            }
            req.idempotency_key = options["idempotency_key"].as_str().map(str::to_owned);
            Client::new(&self.workspace)?.call(&req).await
        }
        .await;
        let (value, error) = match result {
            Ok(r) => {
                let error = !r.ok;
                (serde_json::to_value(r).unwrap(), error)
            }
            Err(e) => (
                json!({"api_version":"1","ok":false,"error":{"code":"CONNECTION_ERROR","message":format!("{e:#}")}}),
                true,
            ),
        };
        Ok(if error {
            CallToolResult::structured_error(value)
        } else {
            CallToolResult::structured(value)
        }
        .into())
    }
}
async fn session(workspace: PathBuf) -> Result<i32> {
    use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt};
    let mut input = tokio::io::BufReader::new(tokio::io::stdin());
    let mut output = tokio::io::stdout();
    let mut session = Client::new(workspace)?.session().await?;
    loop {
        let mut line = Vec::new();
        let n = (&mut input)
            .take((rowtrail_contracts::FRAME_LIMIT + 2) as u64)
            .read_until(b'\n', &mut line)
            .await?;
        if n == 0 {
            break;
        }
        ensure!(
            line.len() <= rowtrail_contracts::FRAME_LIMIT + 1,
            "PROTOCOL_FRAME_TOO_LARGE"
        );
        let request = serde_json::from_slice::<Request>(&line);
        let response = match request {
            Ok(request) => session.call(&request).await?,
            Err(_) => Response::failure(
                &Request::new("invalid", json!({})),
                ApiError::new(
                    "INVALID_ARGUMENT",
                    "expected a full RowTrail request envelope",
                ),
            ),
        };
        output.write_all(&serde_json::to_vec(&response)?).await?;
        output.write_all(b"\n").await?;
        output.flush().await?;
    }
    Ok(0)
}

async fn run(args: Args) -> Result<i32> {
    let workspace = args
        .workspace
        .or_else(|| std::env::var_os("ROWTRAIL_WORKSPACE").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from(".rowtrail"));
    if let Command::Schema { method } = &args.command {
        println!(
            "{}",
            serde_json::to_string_pretty(
                &rowtrail_contracts::schema(method)
                    .ok_or_else(|| anyhow::anyhow!("unknown method"))?
            )?
        );
        return Ok(0);
    }
    if matches!(args.command, Command::Mcp) {
        Mcp { workspace }
            .serve(rmcp::transport::stdio())
            .await?
            .waiting()
            .await?;
        return Ok(0);
    }
    if matches!(args.command, Command::Session) {
        return session(workspace).await;
    }
    let client = Client::new(workspace)?;
    let mut wait = false;
    let mut req = match args.command {
        Command::Doctor => Request::new("doctor", json!({})),
        Command::Call { method, request } => {
            let req = request_from(&method, load(&request)?)?;
            wait = req.method == "control" && req.params["action"] == "wait";
            req
        }
        Command::Open {
            source,
            format,
            schema,
            delimiter,
            no_header,
        } => Request::new(
            "open",
            json!({"source":absolute(&source)?,"format":format,"schema":schema.map(|p|load(&p)).transpose()?,"delimiter":delimiter,"header":!no_header}),
        ),
        Command::Inspect {
            reference,
            revision,
            top_k,
            columns,
            checks,
            offset,
            max_rows,
            max_bytes,
        } => Request::new(
            "inspect",
            json!({"ref":reference,"revision":revision,"top_k":top_k,"columns":columns,"checks":checks,"offset":offset,"budget":{"max_rows":max_rows,"max_bytes":max_bytes}}),
        ),
        Command::Prepare {
            dataset,
            manifest,
            wait_ms,
        } => Request::new(
            "prepare",
            json!({"source":{"dataset_ref":dataset,"manifest_ref":manifest},"execution":{"wait_ms":wait_ms}}),
        ),
        Command::Workspace {
            action,
            quota_bytes,
            apply,
        } => Request::new(
            "workspace",
            json!({"action":action,"quota_bytes":quota_bytes,"dry_run":!apply}),
        ),
        Command::Pin { reference } => {
            Request::new("control", json!({"action":"pin","ref":reference}))
        }
        Command::Release { reference } => {
            Request::new("control", json!({"action":"release","ref":reference}))
        }
        Command::Query {
            request,
            sql,
            bind,
            r#async,
            wait_ms,
        } => {
            let mut req = if let Some(path) = request {
                request_from("query", load(&path)?)?
            } else {
                let p = QueryParams {
                    bindings: parse_bindings(bind)?,
                    sql: sql.ok_or_else(|| anyhow::anyhow!("--sql or --request required"))?,
                    parameters: vec![],
                    execution: Execution::default(),
                    notify: Notify::default(),
                };
                Request::new("query", serde_json::to_value(p)?)
            };
            if r#async || wait_ms.is_some() {
                if req.params.get("execution").is_none() {
                    req.params["execution"] = json!({})
                }
                req.params["execution"]["wait_ms"] =
                    json!(if r#async { 0 } else { wait_ms.unwrap() });
            }
            req
        }
        Command::Read {
            result,
            revision,
            cursor,
            columns,
            max_rows,
            max_bytes,
        } => Request::new(
            "read",
            json!({"result_ref":result,"revision":revision,"cursor":cursor,"columns":columns,"max_rows":max_rows,"max_bytes":max_bytes}),
        ),
        Command::Job { action } => match action {
            JobCommand::Get { job } => {
                Request::new("control", json!({"action":"status","ref":job}))
            }
            JobCommand::Wait { job, wait_ms } => {
                wait = true;
                Request::new(
                    "control",
                    json!({"action":"wait","ref":job,"wait_ms":wait_ms}),
                )
            }
            JobCommand::Cancel { job } => {
                Request::new("control", json!({"action":"cancel","ref":job}))
            }
        },
        Command::Export {
            result,
            revision,
            format,
            output,
            allow_nonfinal,
            allow_estimate,
        } => Request::new(
            "export",
            json!({"result_ref":result,"revision":revision,"format":format,"destination":absolute(&output)?,"allow_nonfinal":allow_nonfinal,"allow_estimate":allow_estimate}),
        ),
        Command::Events {
            mut after,
            job,
            types,
            follow,
            jsonl: _,
        } => loop {
            let req = Request::new(
                "events",
                json!({"after_cursor":after,"job_ids":job,"types":types,"wait_ms":if follow{1000}else{0}}),
            );
            let response = client.call(&req).await?;
            println!("{}", serde_json::to_string(&response)?);
            if !follow || !response.ok {
                return Ok(exit_code(&response, false));
            }
            after = response
                .result
                .as_ref()
                .and_then(|v| v["next_cursor"].as_str())
                .map(str::to_owned);
        },
        Command::Schema { .. } | Command::Mcp | Command::Session => unreachable!(),
    };
    if args.idempotency_key.is_some() {
        req.idempotency_key = args.idempotency_key;
    }
    let response = client.call(&req).await?;
    println!("{}", serde_json::to_string(&response)?);
    Ok(exit_code(&response, wait))
}
#[tokio::main(worker_threads = 2)]
async fn main() {
    let code = match run(Args::parse()).await {
        Ok(code) => code,
        Err(e) => {
            let req = Request::new("cli", json!({}));
            let response = Response::failure(&req, ApiError::new("CLI_ERROR", format!("{e:#}")));
            println!("{}", serde_json::to_string(&response).unwrap());
            5
        }
    };
    std::process::exit(code);
}
