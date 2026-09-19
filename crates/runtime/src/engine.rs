use crate::{
    model::JobSpec,
    sources::parse_type,
    store::{CountStore, Counters},
};
use anyhow::{Result, ensure};
use datafusion::{
    common::ScalarValue,
    datasource::{
        file_format::{FileFormat, arrow::ArrowFormat, csv::CsvFormat, parquet::ParquetFormat},
        listing::{ListingOptions, ListingTable, ListingTableConfig, ListingTableUrl},
    },
    execution::{context::SQLOptions, runtime_env::RuntimeEnvBuilder},
    prelude::*,
};
use std::sync::Arc;

pub async fn plan(
    spec: &JobSpec,
    counters: Arc<Counters>,
) -> Result<(SessionContext, datafusion::dataframe::DataFrame)> {
    let query = spec.query.as_ref().unwrap();
    let e = &query.execution;
    ensure!(
        e.goal == "exact" && matches!(e.preview.as_str(), "none" | "available"),
        "UNSUPPORTED_OPERATION: SQL supports exact with none/available preview"
    );
    let temp = spec.workspace.join("spill").join(&spec.attempt);
    std::fs::create_dir_all(&temp)?;
    let runtime = RuntimeEnvBuilder::new()
        .with_memory_limit(e.memory_bytes, 1.0)
        .with_temp_file_path(temp)
        .with_max_temp_directory_size(e.spill_bytes)
        .build_arc()?;
    let files = spec.inputs.values().flat_map(|i| {
        i.files
            .iter()
            .cloned()
            .map(move |f| (f, i.format != "arrow"))
    });
    runtime.register_object_store(
        &url::Url::parse("file:///")?,
        Arc::new(CountStore::new(files, e.scan_bytes, counters.clone())?),
    );
    let ctx = SessionContext::new_with_config_rt(
        SessionConfig::new()
            .with_target_partitions(1)
            .with_batch_size(1024),
        runtime,
    );
    for (alias, input) in &spec.inputs {
        let format: Arc<dyn FileFormat> = match input.format.as_str() {
            "parquet" => Arc::new(ParquetFormat::default()),
            "arrow" => Arc::new(ArrowFormat),
            _ => Arc::new(
                CsvFormat::default()
                    .with_has_header(input.header)
                    .with_delimiter(input.delimiter)
                    .with_newlines_in_values(true),
            ),
        };
        if input.files.is_empty() {
            ctx.register_table(
                alias.as_str(),
                Arc::new(datafusion::datasource::empty::EmptyTable::new(Arc::new(
                    input.schema.clone(),
                ))),
            )?;
            continue;
        }
        let paths = input
            .files
            .iter()
            .map(|f| ListingTableUrl::parse(f.path.to_str().unwrap()))
            .collect::<datafusion::common::Result<Vec<_>>>()?;
        let options = ListingOptions::new(format).with_file_extension("");
        let provider = ListingTable::try_new(
            ListingTableConfig::new_with_multi_paths(paths)
                .with_listing_options(options)
                .with_schema(Arc::new(input.schema.clone())),
        )?;
        ctx.register_table(alias.as_str(), Arc::new(provider))?;
    }
    let options = SQLOptions::new()
        .with_allow_ddl(false)
        .with_allow_dml(false)
        .with_allow_statements(false);
    let mut df = ctx.sql_with_options(&query.sql, options).await?;
    if !query.parameters.is_empty() {
        let params = query
            .parameters
            .iter()
            .map(|p| {
                let dt = parse_type(&p.r#type)?;
                if p.value.is_null() {
                    Ok(ScalarValue::try_from(&dt)?)
                } else {
                    let s = p
                        .value
                        .as_str()
                        .map(str::to_owned)
                        .unwrap_or_else(|| p.value.to_string());
                    Ok(ScalarValue::try_from_string(s, &dt)?)
                }
            })
            .collect::<Result<Vec<_>>>()?;
        df = df.with_param_values(params)?;
    }
    Ok((ctx, df))
}
