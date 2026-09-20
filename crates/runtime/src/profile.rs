//! Inspection compiles to one ordinary, budgeted query; it never scans on open.
use crate::{db::Db, results, sources::Manifest};
use anyhow::{Result, ensure};
use rowtrail_contracts::*;
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashSet};

pub fn query(db: &Db, p: &InspectParams) -> Result<(QueryParams, Value)> {
    ensure!(
        p.offset == 0,
        "INVALID_ARGUMENT: offset is only for schema inspection"
    );
    let checks = p
        .checks
        .iter()
        .filter(|s| s.as_str() != "schema")
        .map(String::as_str)
        .collect::<HashSet<_>>();
    ensure!(
        checks
            .iter()
            .all(|s| matches!(*s, "head" | "null_count" | "min_max" | "top_k")),
        "UNSUPPORTED_OPERATION: inspect checks"
    );
    ensure!(
        (!checks.contains("head") && !checks.contains("top_k")) || checks.len() == 1,
        "INVALID_ARGUMENT: head and top_k must be separate inspections"
    );
    let (schema, binding) = if p.object_ref.starts_with("res_") {
        ensure!(
            p.revision.is_some(),
            "INVALID_ARGUMENT: row inspection requires a fixed result revision"
        );
        let s = results::snapshot(db, &p.object_ref, p.revision)?;
        (
            s.schema,
            Binding::Result(ResultBinding {
                result_ref: p.object_ref.clone(),
                revision: s.revision,
            }),
        )
    } else {
        ensure!(
            p.revision.is_none(),
            "INVALID_ARGUMENT: revisions apply to results only"
        );
        let mut raw = db.object(&p.object_ref)?;
        if let Some(reference) = raw["manifest_ref"].as_str() {
            raw = db.object(reference)?;
        }
        let m: Manifest = serde_json::from_value(raw)?;
        (
            m.schema,
            Binding::Dataset(DatasetBinding {
                dataset_ref: m.dataset_ref,
                manifest_ref: m.id,
            }),
        )
    };
    let columns = if p.columns.is_empty() {
        schema.fields().iter().map(|f| f.name().clone()).collect()
    } else {
        p.columns.clone()
    };
    ensure!(
        !columns.is_empty() && columns.len() <= 64,
        "INVALID_ARGUMENT: select 1..64 columns per row inspection"
    );
    ensure!(
        columns.iter().collect::<HashSet<_>>().len() == columns.len(),
        "INVALID_ARGUMENT: duplicate inspection column"
    );
    for c in &columns {
        ensure!(
            schema.index_of(c).is_ok(),
            "INVALID_ARGUMENT: unknown inspection column {c}"
        );
    }
    let quoted = columns
        .iter()
        .map(|s| format!("\"{}\"", s.replace('"', "\"\"")))
        .collect::<Vec<_>>();
    let mut fields = vec![json!({"output":"rows","check":"row_count"})];
    let sql = if checks.contains("head") {
        ensure!(
            p.budget.max_rows > 0,
            "INVALID_ARGUMENT: head requires positive max_rows"
        );
        format!(
            "SELECT {} FROM source LIMIT {}",
            quoted.join(","),
            p.budget.max_rows
        )
    } else if checks.contains("top_k") {
        ensure!(
            columns.len() == 1 && (1..=1000).contains(&p.top_k),
            "INVALID_ARGUMENT: top_k requires one column and k=1..1000"
        );
        fields = vec![
            json!({"output":"value","column":columns[0]}),
            json!({"output":"count","check":"frequency"}),
        ];
        format!(
            "SELECT {} AS value, COUNT(*) AS count FROM source GROUP BY {} ORDER BY count DESC, value ASC NULLS LAST LIMIT {}",
            quoted[0], quoted[0], p.top_k
        )
    } else {
        let mut expressions = vec!["COUNT(*) AS rows".to_owned()];
        for (i, q) in quoted.iter().enumerate() {
            if checks.contains("null_count") {
                expressions.push(format!("COUNT(*) - COUNT({q}) AS c{i}_null_count"));
                fields.push(json!({"output":format!("c{i}_null_count"),"column":columns[i],"check":"null_count"}));
            }
            if checks.contains("min_max") {
                for check in ["min", "max"] {
                    expressions.push(format!("{check}({q}) AS c{i}_{check}"));
                    fields.push(
                        json!({"output":format!("c{i}_{check}"),"column":columns[i],"check":check}),
                    );
                }
            }
        }
        format!("SELECT {} FROM source", expressions.join(","))
    };
    let mut execution = p.execution.clone().unwrap_or_else(|| {
        if checks.contains("head") {
            Execution {
                scan_bytes: 1024 * 1024,
                run_timeout_ms: 1000,
                ..Default::default()
            }
        } else {
            Execution::default()
        }
    });
    execution.output = p.budget.clone();
    let info = json!({"columns":columns,"checks":p.checks,"outputs":fields,"top_k":if checks.contains("top_k"){Some(p.top_k)}else{None}});
    Ok((
        QueryParams {
            bindings: BTreeMap::from([("source".into(), binding)]),
            sql,
            parameters: vec![],
            execution,
            notify: Notify::default(),
        },
        info,
    ))
}
