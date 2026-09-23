//! Optional transport projection. Stored jobs, quality and numeric values stay intact.
use serde_json::{Value, json};

pub fn compact(value: &mut Value) {
    if let Some(job) = value.get_mut("job").and_then(Value::as_object_mut) {
        job.retain(|key, _| {
            matches!(
                key.as_str(),
                "id" | "state" | "result_ref" | "scope_ref" | "error" | "prepared"
            )
        });
        job.retain(|_, value| !value.is_null());
        for key in ["job_id", "result_ref", "view_ref", "scope_ref"] {
            value.as_object_mut().unwrap().remove(key);
        }
        value["details_omitted"] = json!(true);
    }
    if let Some(observation) = value.get_mut("observation").and_then(Value::as_object_mut) {
        observation.remove("read_metrics");
    }
    if let Some(object) = value.as_object_mut() {
        object.remove("read_metrics");
    }
}
