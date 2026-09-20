use crate::sources::{Manifest, SourceFile};
use arrow::datatypes::Schema;
use rowtrail_contracts::{AnalyzeParams, DatasetBinding, ExportParams, QueryParams};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::BTreeMap, path::PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Input {
    pub format: String,
    pub schema: Schema,
    pub files: Vec<SourceFile>,
    pub header: bool,
    pub delimiter: u8,
    pub source_ref: String,
    pub quality: Value,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobSpec {
    pub job_id: String,
    pub attempt: String,
    pub result_ref: String,
    pub scope_ref: String,
    pub workspace: PathBuf,
    pub query: Option<QueryParams>,
    pub export: Option<ExportParams>,
    #[serde(default)]
    pub prepared: Option<DatasetBinding>,
    #[serde(default)]
    pub analysis: Option<AnalyzeParams>,
    pub inputs: BTreeMap<String, Input>,
    pub sources: Vec<Manifest>,
    pub quality: Value,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Part {
    pub seq: u64,
    pub path: PathBuf,
    pub bytes: u64,
    pub rows: usize,
    pub checksum: String,
    pub schema: Schema,
    #[serde(default)]
    pub checkpoint: Option<Checkpoint>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Checkpoint {
    pub completed_files: usize,
    pub total_files: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WorkerMessage {
    Schema {
        schema: Schema,
    },
    Part {
        part: Part,
    },
    Completed {
        metrics: Value,
    },
    Failed {
        code: String,
        message: String,
        metrics: Value,
    },
}
