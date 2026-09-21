//! Restricted progressive aggregation over complete Parquet fragments.
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
        matches!(
            p.fragment_unit.as_str(),
            "manifest_file" | "parquet_row_group"
        ),
        "UNSUPPORTED_OPERATION: fragment_unit"
    );
    ensure!(
        p.checkpoint_interval_ms <= 60_000,
        "INVALID_ARGUMENT: checkpoint_interval_ms exceeds 60000"
    );
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

fn accumulate(p: &AnalyzeParams, states: &mut [State], batch: &RecordBatch) -> Result<()> {
    for (a, state) in p.aggregates.iter().zip(states) {
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
    Ok(())
}

pub async fn execute(
    spec: &JobSpec,
    counters: Arc<Counters>,
    ack: &mut tokio::sync::mpsc::Receiver<String>,
) -> Result<serde_json::Value> {
    use datafusion::{
        execution::runtime_env::RuntimeEnvBuilder,
        prelude::{SessionConfig, SessionContext},
    };
    use datafusion_datasource::{
        PartitionedFile, file_scan_config::FileScanConfigBuilder, source::DataSourceExec,
    };
    use datafusion_datasource_parquet::{
        CachedParquetFileReaderFactory, ParquetAccessPlan, ParquetFileReaderFactory,
        source::ParquetSource,
    };
    use datafusion_execution::object_store::ObjectStoreUrl;
    use datafusion_physical_plan::{ExecutionPlan, metrics::ExecutionPlanMetricsSet};
    let started = std::time::Instant::now();
    let p = spec.analysis.as_ref().unwrap();
    let input = spec.inputs.values().next().unwrap();
    ensure!(
        !input.files.is_empty(),
        "INVALID_ARGUMENT: analysis requires a Parquet file"
    );
    let store = Arc::new(crate::store::CountStore::new(
        input.files.iter().cloned().map(|f| (f, true)),
        p.execution.scan_bytes,
        counters.clone(),
    )?);
    let runtime = RuntimeEnvBuilder::new()
        .with_memory_limit(p.execution.memory_bytes, 1.0)
        .with_metadata_cache_limit((p.execution.memory_bytes / 8).min(8 * 1024 * 1024))
        .build_arc()?;
    runtime.register_object_store(&url::Url::parse("file:///")?, store.clone());
    let factory = Arc::new(CachedParquetFileReaderFactory::new(
        store,
        runtime.cache_manager.get_file_metadata_cache(),
    ));
    let ctx = SessionContext::new_with_config_rt(
        SessionConfig::new()
            .with_target_partitions(1)
            .with_batch_size(8192),
        runtime,
    );
    let columns = p
        .aggregates
        .iter()
        .filter_map(|a| a.column.as_ref())
        .map(|name| input.schema.index_of(name))
        .collect::<std::result::Result<BTreeSet<_>, _>>()?
        .into_iter()
        .collect::<Vec<_>>();
    let source = Arc::new(
        ParquetSource::new(Arc::new(input.schema.clone()))
            .with_parquet_file_reader_factory(factory.clone()),
    );
    let mut states = p
        .aggregates
        .iter()
        .map(|a| State {
            sum: 0,
            count: 0,
            scale: a
                .column
                .as_ref()
                .and_then(|c| input.schema.field_with_name(c).ok())
                .map(|f| match f.data_type() {
                    DataType::Decimal128(_, s) => *s,
                    _ => 0,
                })
                .unwrap_or(0),
        })
        .collect::<Vec<_>>();
    crate::worker::analysis_schema(output(p, &states)?.schema().as_ref().clone()).await?;
    let file_mode = p.fragment_unit == "manifest_file";
    let total_fragments = if file_mode {
        Some(input.files.len())
    } else {
        input
            .files
            .iter()
            .map(|f| f.row_groups)
            .sum::<Option<usize>>()
    };
    let mut progress = Checkpoint {
        unit: p.fragment_unit.clone(),
        completed_files: 0,
        total_files: input.files.len(),
        completed_fragments: 0,
        total_fragments,
        processed_rows: 0,
    };
    let mut seq = 0;
    let mut written = 0;
    let mut published = None;
    let mut last_publish = started;
    for (file_index, file) in input.files.iter().enumerate() {
        file.validate()?;
        let path = object_store::path::Path::from_filesystem_path(&file.path)?;
        let partitioned = PartitionedFile::new(path.to_string(), file.size);
        let mut reader = factory.create_reader(
            0,
            partitioned.clone(),
            None,
            &ExecutionPlanMetricsSet::new(),
        )?;
        let metadata = reader.get_metadata(None).await?;
        if let Some(expected) = file.row_groups {
            ensure!(
                metadata.num_row_groups() == expected,
                "SOURCE_CHANGED: Parquet row groups"
            );
        }
        if let Some(expected) = file.rows {
            ensure!(
                metadata.file_metadata().num_rows() as u64 == expected,
                "SOURCE_CHANGED: Parquet row count"
            );
        }
        // One execution context and bounded metadata cache per job; no repeated SQL planning.
        let fragments = if file_mode {
            1
        } else {
            metadata.num_row_groups().max(1)
        };
        for index in 0..fragments {
            let plan = if file_mode {
                ParquetAccessPlan::new_all(metadata.num_row_groups())
            } else {
                let mut plan = ParquetAccessPlan::new_none(metadata.num_row_groups());
                if metadata.num_row_groups() > 0 {
                    plan.scan(index);
                }
                plan
            };
            let config =
                FileScanConfigBuilder::new(ObjectStoreUrl::local_filesystem(), source.clone())
                    .with_file(partitioned.clone().with_extension(plan))
                    .with_projection_indices(Some(columns.clone()))?
                    .build();
            let exec = DataSourceExec::from_data_source(config);
            let mut stream = exec.execute(0, ctx.task_ctx())?;
            let mut rows = 0u64;
            while let Some(batch) = stream.next().await {
                let batch = batch?;
                rows += batch.num_rows() as u64;
                accumulate(p, &mut states, &batch)?;
            }
            let expected = if file_mode {
                metadata.file_metadata().num_rows()
            } else {
                metadata
                    .row_groups()
                    .get(index)
                    .map(|r| r.num_rows())
                    .unwrap_or(0)
            };
            ensure!(
                rows == expected as u64,
                "RESULT_CORRUPT: incomplete Parquet fragment"
            );
            file.validate()?;
            progress.processed_rows += rows;
            if file_mode || metadata.num_row_groups() > 0 {
                progress.completed_fragments += 1;
            }
            if index + 1 == fragments {
                progress.completed_files = file_index + 1;
            }
            let final_fragment = progress.completed_files == progress.total_files;
            if final_fragment {
                progress.total_fragments = Some(progress.completed_fragments);
            }
            if (p.execution.preview == "available"
                && (seq == 0
                    || last_publish.elapsed().as_millis() >= u128::from(p.checkpoint_interval_ms)))
                || final_fragment
            {
                let key = (progress.completed_fragments, progress.completed_files);
                if published != Some(key) {
                    written += crate::worker::checkpoint(
                        spec,
                        &output(p, &states)?,
                        seq,
                        p.execution.result_bytes.saturating_sub(written),
                        progress.clone(),
                        ack,
                    )
                    .await?;
                    seq += 1;
                    published = Some(key);
                    last_publish = std::time::Instant::now();
                }
            }
        }
    }
    for source in &spec.sources {
        source.validate()?;
    }
    Ok(
        json!({"io":counters.value(),"rows":1,"input_rows_processed":progress.processed_rows,"files_completed":progress.completed_files,"files_total":progress.total_files,"fragments_completed":progress.completed_fragments,"fragment_unit":progress.unit,"result_write_bytes":written,"checkpoints":seq,"checkpoint_interval_ms":p.checkpoint_interval_ms,"elapsed_ms":started.elapsed().as_secs_f64()*1000.0}),
    )
}
