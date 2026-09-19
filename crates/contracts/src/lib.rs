use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::BTreeMap;

pub const API_VERSION: &str = "1";
pub const FRAME_LIMIT: usize = 1024 * 1024;
pub fn id(prefix: &str) -> String {
    format!("{prefix}_{}", uuid::Uuid::new_v4().simple())
}
pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub api_version: String,
    pub request_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub idempotency_key: Option<String>,
    pub method: String,
    pub params: Value,
}
impl Request {
    pub fn new(method: &str, params: Value) -> Self {
        Self {
            api_version: API_VERSION.into(),
            request_id: id("req"),
            idempotency_key: None,
            method: method.into(),
            params,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ApiError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub details: Value,
}
impl ApiError {
    pub fn new(code: &str, message: impl ToString) -> Self {
        Self {
            code: code.into(),
            message: message.to_string(),
            retryable: false,
            details: json!({}),
        }
    }
}
impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for ApiError {}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct Response {
    pub api_version: String,
    pub request_id: String,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ApiError>,
}
impl Response {
    pub fn success(req: &Request, v: Value) -> Self {
        Self {
            api_version: API_VERSION.into(),
            request_id: req.request_id.clone(),
            ok: true,
            result: Some(v),
            error: None,
        }
    }
    pub fn failure(req: &Request, e: ApiError) -> Self {
        Self {
            api_version: API_VERSION.into(),
            request_id: req.request_id.clone(),
            ok: false,
            result: None,
            error: Some(e),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Column {
    pub name: String,
    pub r#type: String,
    #[serde(default = "yes")]
    pub nullable: bool,
}
fn yes() -> bool {
    true
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(default, deny_unknown_fields)]
pub struct OutputBudget {
    pub max_rows: usize,
    pub max_bytes: usize,
}
impl Default for OutputBudget {
    fn default() -> Self {
        Self {
            max_rows: 100,
            max_bytes: 8192,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(default, deny_unknown_fields)]
pub struct Execution {
    pub goal: String,
    pub preview: String,
    pub wait_ms: u64,
    pub run_timeout_ms: u64,
    pub memory_bytes: usize,
    pub scan_bytes: u64,
    pub result_bytes: u64,
    pub spill_bytes: u64,
    pub output: OutputBudget,
}
impl Default for Execution {
    fn default() -> Self {
        Self {
            goal: "exact".into(),
            preview: "available".into(),
            wait_ms: 200,
            run_timeout_ms: 60_000,
            memory_bytes: 128 * 1024 * 1024,
            scan_bytes: 1024 * 1024 * 1024,
            result_bytes: 512 * 1024 * 1024,
            spill_bytes: 512 * 1024 * 1024,
            output: OutputBudget::default(),
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct OpenParams {
    pub source: String,
    #[serde(default = "auto")]
    pub format: String,
    #[serde(default)]
    pub schema: Option<Vec<Column>>,
    #[serde(default = "yes")]
    pub header: bool,
    #[serde(default)]
    pub delimiter: Option<String>,
    #[serde(default = "infer_rows")]
    pub infer_rows: usize,
    #[serde(default)]
    pub output: OutputBudget,
}
fn auto() -> String {
    "auto".into()
}
fn infer_rows() -> usize {
    100
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DatasetBinding {
    pub dataset_ref: String,
    pub manifest_ref: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ResultBinding {
    pub result_ref: String,
    pub revision: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(untagged)]
pub enum Binding {
    Dataset(DatasetBinding),
    Result(ResultBinding),
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Parameter {
    pub r#type: String,
    pub value: Value,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, Default)]
#[serde(default, deny_unknown_fields)]
pub struct Notify {
    pub correlation_id: Option<String>,
    pub types: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct QueryParams {
    pub bindings: BTreeMap<String, Binding>,
    pub sql: String,
    #[serde(default)]
    pub parameters: Vec<Parameter>,
    #[serde(default)]
    pub execution: Execution,
    #[serde(default)]
    pub notify: Notify,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct InspectParams {
    #[serde(rename = "ref")]
    pub object_ref: String,
    #[serde(default)]
    pub columns: Vec<String>,
    #[serde(default)]
    pub checks: Vec<String>,
    #[serde(default)]
    pub offset: usize,
    #[serde(default)]
    pub budget: OutputBudget,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ReadParams {
    pub result_ref: String,
    #[serde(default)]
    pub revision: Option<u64>,
    #[serde(default)]
    pub cursor: Option<String>,
    #[serde(default)]
    pub columns: Vec<String>,
    #[serde(default = "read_rows")]
    pub max_rows: usize,
    #[serde(default = "read_bytes")]
    pub max_bytes: usize,
}
fn read_rows() -> usize {
    100
}
fn read_bytes() -> usize {
    8192
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ControlParams {
    pub action: String,
    #[serde(rename = "ref")]
    pub object_ref: String,
    #[serde(default)]
    pub wait_ms: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ExportParams {
    pub result_ref: String,
    pub revision: u64,
    pub format: String,
    pub destination: String,
    #[serde(default)]
    pub allow_nonfinal: bool,
    #[serde(default)]
    pub allow_estimate: bool,
    #[serde(default)]
    pub execution: Execution,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(default, deny_unknown_fields)]
pub struct EventsParams {
    pub after_cursor: Option<String>,
    pub job_ids: Vec<String>,
    pub types: Vec<String>,
    pub limit: usize,
    pub max_bytes: usize,
    pub wait_ms: u64,
}
impl Default for EventsParams {
    fn default() -> Self {
        Self {
            after_cursor: None,
            job_ids: vec![],
            types: vec![],
            limit: 100,
            max_bytes: 8192,
            wait_ms: 0,
        }
    }
}

pub fn schema(method: &str) -> Option<Value> {
    Some(match method {
        "open" => serde_json::to_value(schemars::schema_for!(OpenParams)).ok()?,
        "inspect" => serde_json::to_value(schemars::schema_for!(InspectParams)).ok()?,
        "query" => serde_json::to_value(schemars::schema_for!(QueryParams)).ok()?,
        "read" => serde_json::to_value(schemars::schema_for!(ReadParams)).ok()?,
        "control" => serde_json::to_value(schemars::schema_for!(ControlParams)).ok()?,
        "export" => serde_json::to_value(schemars::schema_for!(ExportParams)).ok()?,
        "events" => serde_json::to_value(schemars::schema_for!(EventsParams)).ok()?,
        "request" => serde_json::to_value(schemars::schema_for!(Request)).ok()?,
        "response" => serde_json::to_value(schemars::schema_for!(Response)).ok()?,
        _ => return None,
    })
}
pub fn terminal(state: &str) -> bool {
    matches!(
        state,
        "completed" | "failed" | "cancelled" | "budget_exhausted" | "interrupted"
    )
}
