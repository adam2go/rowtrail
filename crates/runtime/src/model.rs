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
    /// Hex encoded IPC, bounded to INLINE_LIMIT decoded bytes.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub inline_data: Option<String>,
    #[serde(default)]
    pub checkpoint: Option<Checkpoint>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Checkpoint {
    pub unit: String,
    pub completed_fragments: usize,
    pub total_fragments: Option<usize>,
    pub processed_rows: u64,
    pub completed_files: usize,
    pub total_files: usize,
}
impl Checkpoint {
    pub fn coverage(&self) -> Value {
        if self.unit == "manifest_file" {
            serde_json::json!({"unit":self.unit,"completed_files":self.completed_files,"total_files":self.total_files,"order":"frozen_manifest_order"})
        } else {
            serde_json::json!({"unit":self.unit,"completed_files":self.completed_files,"total_files":self.total_files,"completed_fragments":self.completed_fragments,"total_fragments":self.total_fragments,"processed_rows":self.processed_rows,"order":"frozen_manifest_order_then_row_group"})
        }
    }
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
        #[serde(default)]
        details: Value,
        metrics: Value,
    },
}
