//! Restricted progressive aggregation over complete files in a frozen manifest.
//! Checkpoints replace the previous aggregate row; they are not appended answers.
use crate::{
    model::{Checkpoint, Input, JobSpec},
    store::Counters,
};
use anyhow::{Result, bail, ensure};
use arrow::{
    array::{Array, ArrayRef, Decimal128Array, Int64Array, UInt64Array},
    datatypes::{DataType, Field, Schema},
    record_batch::RecordBatch,
};
use futures::StreamExt;
use rowtrail_contracts::AnalyzeParams;
use serde_json::json;
use std::{collections::BTreeSet, sync::Arc};

pub fn projection(input: &Input, p: &AnalyzeParams) -> Result<String> {
    ensure!(
        !p.aggregates.is_empty() && p.aggregates.len() <= 16,
        "INVALID_ARGUMENT: select 1..16 aggregates"
    );
    let mut aliases = BTreeSet::new();
    let mut columns = BTreeSet::new();
    for a in &p.aggregates {
        ensure!(
            !a.alias.is_empty() && a.alias.len() <= 128 && aliases.insert(&a.alias),
            "INVALID_ARGUMENT: aggregate aliases must be unique, nonempty and at most 128 bytes"
        );
        ensure!(
            matches!(a.function.as_str(), "count" | "sum" | "avg"),
            "UNSUPPORTED_OPERATION: aggregate function"
        );
        if let Some(name) = &a.column {
            let index = input.schema.index_of(name).map_err(|_| {
                anyhow::anyhow!("INVALID_ARGUMENT: unknown aggregate column {name}")
            })?;
            if a.function != "count" {
                ensure!(
                    matches!(
                        input.schema.field(index).data_type(),
                        DataType::Int64 | DataType::UInt64 | DataType::Decimal128(_, 0..=6)
                    ),
                    "UNSUPPORTED_OPERATION: sum/avg accepts Int64, UInt64 or Decimal128 with scale 0..6"
                );
            }
            columns.insert(name);
        } else {
            ensure!(
                a.function == "count",
                "INVALID_ARGUMENT: sum/avg needs a column"
            );
        }
    }
    Ok(if columns.is_empty() {
        "1 AS __rowtrail_row".into()
    } else {
        columns
            .into_iter()
            .map(|c| format!("\"{}\"", c.replace('"', "\"\"")))
            .collect::<Vec<_>>()
            .join(",")
    })
}
struct State {
    sum: i128,
    count: u64,
    scale: i8,
}
fn add(state: &mut State, value: i128) -> Result<()> {
    state.sum = state
        .sum
        .checked_add(value)
        .ok_or_else(|| anyhow::anyhow!("ARITHMETIC_OVERFLOW: aggregate sum exceeds i128"))?;
    Ok(())
}
fn count(state: &mut State, n: usize) -> Result<()> {
    state.count = state
        .count
        .checked_add(n as u64)
        .ok_or_else(|| anyhow::anyhow!("ARITHMETIC_OVERFLOW: count"))?;
    Ok(())
}
fn output(p: &AnalyzeParams, states: &[State]) -> Result<RecordBatch> {
    let mut arrays: Vec<ArrayRef> = vec![];
    let mut fields = vec![];
    for (a, state) in p.aggregates.iter().zip(states) {
        if a.function == "count" {
            fields.push(Field::new(&a.alias, DataType::UInt64, false));
            arrays.push(Arc::new(UInt64Array::from(vec![state.count])));
        } else {
            let scale = if a.function == "avg" { 6 } else { state.scale };
            let value = if state.count == 0 {
                None
            } else if a.function == "avg" {
                // Split integer/remainder first so a large sum times 10^6 does
                // not overflow even when the representable average is small.
                let divisor = i128::from(state.count);
                let factor = 10i128.pow((6 - state.scale) as u32);
                let integer = (state.sum / divisor).checked_mul(factor);
                let fraction = (state.sum % divisor)
                    .checked_mul(factor)
                    .map(|v| v / divisor);
                Some(
                    integer
                        .and_then(|v| fraction.and_then(|f| v.checked_add(f)))
                        .ok_or_else(|| {
                            anyhow::anyhow!("ARITHMETIC_OVERFLOW: average at scale 6")
                        })?,
                )
            } else {
                Some(state.sum)
            };
            if let Some(value) = value {
                ensure!(
                    value.unsigned_abs() < 10u128.pow(38),
                    "ARITHMETIC_OVERFLOW: Decimal128(38,{scale})"
                );
            }
            let array = Decimal128Array::from(vec![value]).with_precision_and_scale(38, scale)?;
            fields.push(Field::new(&a.alias, DataType::Decimal128(38, scale), true));
            arrays.push(Arc::new(array));
        }
    }
    Ok(RecordBatch::try_new(Arc::new(Schema::new(fields)), arrays)?)
}

pub async fn execute(
    spec: &JobSpec,
    counters: Arc<Counters>,
    ack: &mut tokio::sync::mpsc::Receiver<String>,
) -> Result<serde_json::Value> {
    let started = std::time::Instant::now();
    let p = spec.analysis.as_ref().unwrap();
    let input = spec.inputs.values().next().unwrap();
    let total = input.files.len();
    ensure!(
        total > 0,
        "INVALID_ARGUMENT: analysis requires at least one Parquet file"
    );
    let mut states = p
        .aggregates
        .iter()
        .map(|a| {
            let scale = a
                .column
                .as_ref()
                .and_then(|c| input.schema.field_with_name(c).ok())
                .map(|f| match f.data_type() {
                    DataType::Decimal128(_, s) => *s,
                    _ => 0,
                })
                .unwrap_or(0);
            State {
                sum: 0,
                count: 0,
                scale,
            }
        })
        .collect::<Vec<_>>();
    crate::worker::analysis_schema(output(p, &states)?.schema().as_ref().clone()).await?;
    let mut seq = 0;
    let mut written = 0;
    let mut rows_scanned = 0u64;
    for (index, file) in input.files.iter().enumerate() {
        file.validate()?;
        let mut fragment = spec.clone();
        fragment.inputs.get_mut("source").unwrap().files = vec![file.clone()];
        let (ctx, df) = crate::engine::plan(&fragment, counters.clone()).await?;
        let mut stream = df.execute_stream().await?;
        while let Some(batch) = stream.next().await {
            let batch = batch?;
            rows_scanned += batch.num_rows() as u64;
            for (a, state) in p.aggregates.iter().zip(&mut states) {
                let Some(column) = &a.column else {
                    count(state, batch.num_rows())?;
                    continue;
                };
                let array = batch
                    .column_by_name(column)
                    .ok_or_else(|| anyhow::anyhow!("RESULT_CORRUPT: analysis projection"))?;
                count(state, array.len() - array.null_count())?;
                if a.function == "count" {
                    continue;
                }
                if let Some(array) = array.as_any().downcast_ref::<Int64Array>() {
                    for value in array.iter().flatten() {
                        add(state, i128::from(value))?;
                    }
                } else if let Some(array) = array.as_any().downcast_ref::<UInt64Array>() {
                    for value in array.iter().flatten() {
                        add(state, i128::from(value))?;
                    }
                } else if let Some(array) = array.as_any().downcast_ref::<Decimal128Array>() {
                    for value in array.iter().flatten() {
                        add(state, value)?;
                    }
                } else {
                    bail!("UNSUPPORTED_OPERATION: aggregate numeric type");
                }
            }
        }
        drop(stream);
        drop(ctx);
        file.validate()?;
        if p.execution.preview == "available" || index + 1 == total {
            let batch = output(p, &states)?;
            written += crate::worker::checkpoint(
                spec,
                &batch,
                seq,
                p.execution.result_bytes.saturating_sub(written),
                Checkpoint {
                    completed_files: index + 1,
                    total_files: total,
                },
                ack,
            )
            .await?;
            seq += 1;
        }
    }
    for source in &spec.sources {
        source.validate()?;
    }
    Ok(
        json!({"io":counters.value(),"rows":1,"input_rows_processed":rows_scanned,"files_completed":total,"files_total":total,"result_write_bytes":written,"elapsed_ms":started.elapsed().as_secs_f64()*1000.0,"fragment_unit":"manifest_file"}),
    )
}
