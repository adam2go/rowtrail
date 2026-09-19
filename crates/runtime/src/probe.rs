use anyhow::{Result, ensure};
use arrow::{array::*, datatypes::*, record_batch::RecordBatch};
use datafusion::{
    execution::runtime_env::RuntimeEnvBuilder,
    physical_plan::{display::DisplayableExecutionPlan, execute_stream},
    prelude::*,
};
use futures::StreamExt;
use parquet::{arrow::ArrowWriter, file::properties::WriterProperties};
use serde_json::json;
use std::{
    fs::File,
    path::Path,
    sync::Arc,
    time::{Duration, Instant},
};

pub fn generate(directory: &Path, rows: usize) -> Result<()> {
    std::fs::create_dir_all(directory)?;
    std::fs::write(
        directory.join("small.csv"),
        "id,date,region,product,amount,note\n9007199254740993,2026-08-01,华东,A,10.25,ok\n9007199254740994,2026-08-02,华东,B,20.50,\n9007199254740995,2026-08-03,华南,A,-5.00,退货\n9007199254740996,2026-08-04,华南,B,,missing\n9007199254740997,2026-09-01,华东,A,3.25,ok\n9007199254740998,2026-09-02,华北,A,100.00,ok\n9007199254740999,2026-09-03,华北,B,0.00,zero\n9007199254741000,2026-09-04,,B,1.00,unknown\n",
    )?;
    let small_schema = Arc::new(Schema::new(vec![
        Field::new("id", DataType::Int64, true),
        Field::new("date", DataType::Date32, true),
        Field::new("region", DataType::Utf8, true),
        Field::new("product", DataType::Utf8, true),
        Field::new("amount", DataType::Decimal128(20, 2), true),
        Field::new("note", DataType::Utf8, true),
    ]));
    let mut small = ArrowWriter::try_new(
        File::create(directory.join("small.parquet"))?,
        small_schema.clone(),
        Some(
            WriterProperties::builder()
                .set_max_row_group_row_count(Some(2))
                .build(),
        ),
    )?;
    for batch in arrow::csv::ReaderBuilder::new(small_schema)
        .with_header(true)
        .build(File::open(directory.join("small.csv"))?)?
    {
        small.write(&batch?)?;
    }
    small.close()?;
    let schema = Arc::new(Schema::new(vec![
        Field::new("id", DataType::Int64, false),
        Field::new("region", DataType::Utf8, true),
        Field::new("product", DataType::Utf8, false),
        Field::new("amount", DataType::Decimal128(20, 2), true),
        Field::new("payload", DataType::Utf8, false),
    ]));
    let properties = WriterProperties::builder()
        .set_max_row_group_row_count(Some(4096))
        .build();
    let mut writer = ArrowWriter::try_new(
        File::create(directory.join("many.parquet"))?,
        schema.clone(),
        Some(properties),
    )?;
    for start in (0..rows).step_by(4096) {
        let end = (start + 4096).min(rows);
        let ids =
            Int64Array::from_iter_values((start..end).map(|i| 9_007_199_254_740_993 + i as i64));
        let regions = StringArray::from_iter((start..end).map(|i| {
            if i % 13 == 0 {
                None
            } else {
                Some(["华东", "华南", "华北"][i % 3])
            }
        }));
        let products =
            StringArray::from_iter_values((start..end).map(|i| if i % 2 == 0 { "A" } else { "B" }));
        let amount = Decimal128Array::from_iter((start..end).map(|i| {
            if i % 17 == 0 {
                None
            } else {
                Some((i as i128 % 2001) - 1000)
            }
        }))
        .with_precision_and_scale(20, 2)?;
        let payload = StringArray::from_iter_values((start..end).map(|i| {
            format!(
                "{:016x}-{}",
                (i as u64).wrapping_mul(6364136223846793005),
                "structured-data-探索".repeat(8)
            )
        }));
        writer.write(&RecordBatch::try_new(
            schema.clone(),
            vec![
                Arc::new(ids),
                Arc::new(regions),
                Arc::new(products),
                Arc::new(amount),
                Arc::new(payload),
            ],
        )?)?;
    }
    writer.close()?;
    Ok(())
}

fn cpu_ms() -> f64 {
    let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe {
        libc::getrusage(libc::RUSAGE_SELF, &mut usage);
    }
    (usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) as f64 * 1000.0
        + (usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) as f64 / 1000.0
}

pub async fn run(directory: &Path, rows: usize) -> Result<()> {
    generate(directory, rows)?;
    let spill = directory.join("spill");
    std::fs::create_dir_all(&spill)?;
    let runtime = RuntimeEnvBuilder::new()
        .with_memory_limit(32 * 1024 * 1024, 1.0)
        .with_temp_file_path(&spill)
        .with_max_temp_directory_size(512 * 1024 * 1024)
        .build_arc()?;
    let config = SessionConfig::new()
        .with_target_partitions(1)
        .with_batch_size(1024);
    let ctx = SessionContext::new_with_config_rt(config, runtime);
    ctx.register_csv(
        "small",
        directory.join("small.csv").to_str().unwrap(),
        CsvReadOptions::new(),
    )
    .await?;
    ctx.register_parquet(
        "many",
        directory.join("many.parquet").to_str().unwrap(),
        ParquetReadOptions::default(),
    )
    .await?;
    let started = Instant::now();
    let mut s = ctx
        .sql("SELECT COUNT(*) AS n FROM small")
        .await?
        .execute_stream()
        .await?;
    let batch = s.next().await.unwrap()?;
    ensure!(
        batch
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .unwrap()
            .value(0)
            == 8,
        "CSV count"
    );
    drop(s);
    let csv_ms = started.elapsed().as_secs_f64() * 1000.0;
    let started = Instant::now();
    let df = ctx
        .sql("SELECT id, payload FROM many ORDER BY payload, id DESC")
        .await?;
    let plan = df.create_physical_plan().await?;
    let mut s = execute_stream(plan.clone(), ctx.task_ctx())?;
    let mut count = 0;
    while let Some(b) = s.next().await {
        count += b?.num_rows();
    }
    ensure!(count == rows, "Parquet sort count mismatch");
    let sort_ms = started.elapsed().as_secs_f64() * 1000.0;
    let metrics = format!(
        "{}",
        DisplayableExecutionPlan::with_metrics(plan.as_ref()).indent(true)
    );
    let started = Instant::now();
    let mut s = ctx
        .sql("SELECT COUNT(*) FROM many a CROSS JOIN many b WHERE a.id + b.id > 0")
        .await?
        .execute_stream()
        .await?;
    let outcome = tokio::time::timeout(Duration::from_millis(200), s.next()).await;
    ensure!(
        outcome.is_err(),
        "Cancellation probe completed unexpectedly"
    );
    drop(s);
    tokio::time::sleep(Duration::from_millis(100)).await;
    let before = cpu_ms();
    tokio::time::sleep(Duration::from_millis(200)).await;
    let idle_cpu = cpu_ms() - before;
    ensure!(
        idle_cpu < 100.0,
        "Stream drop did not quiesce CPU: {idle_cpu}ms"
    );
    let report = json!({"datafusion":"55.0.0","rows":rows,"csv_ms":csv_ms,"sort_ms":sort_ms,"sort_plan_metrics":metrics,"cancel_probe_ms":started.elapsed().as_secs_f64()*1000.0,"cpu_ms_after_cancel_in_200ms":idle_cpu,"engine_memory_bytes":33554432,"spill_limit_bytes":536870912});
    std::fs::write(
        directory.join("probe.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

/// Development-only reference execution. Bounded fixture results are kept in a
/// persistent MemTable, a deliberately strong baseline for within-run reuse.
pub async fn baseline(source: &Path) -> Result<()> {
    ensure!(
        source.metadata()?.len() <= 256 * 1024 * 1024,
        "benchmark fixture exceeds 256 MiB bound"
    );
    let started = Instant::now();
    let ctx = SessionContext::new_with_config(SessionConfig::new().with_target_partitions(1));
    ctx.register_parquet("t", source.to_str().unwrap(), ParquetReadOptions::default())
        .await?;
    let mut stages = vec![];
    let queries = [
        "SELECT region, SUM(amount), COUNT(*) FROM t GROUP BY region",
        "SELECT id, region, amount FROM t WHERE amount IS NOT NULL",
        "SELECT region, SUM(amount) FROM saved GROUP BY region",
        "SELECT COUNT(*) FROM saved WHERE amount > 0",
        "SELECT product, COUNT(*) FROM t GROUP BY product",
    ];
    for (index, sql) in queries.into_iter().enumerate() {
        let t = Instant::now();
        let df = ctx.sql(sql).await?;
        let schema = df.schema().as_arrow().clone();
        let plan = df.create_physical_plan().await?;
        let mut stream = execute_stream(plan.clone(), ctx.task_ctx())?;
        let mut rows = 0;
        let mut batches = vec![];
        while let Some(batch) = stream.next().await {
            let batch = batch?;
            rows += batch.num_rows();
            if index == 1 {
                batches.push(batch);
            }
        }
        if index == 1 {
            ctx.register_table(
                "saved",
                Arc::new(datafusion::datasource::MemTable::try_new(
                    Arc::new(schema),
                    vec![batches],
                )?),
            )?;
        }
        stages.push(json!({"query":sql,"rows":rows,"elapsed_ms":t.elapsed().as_secs_f64()*1000.0,"plan":format!("{}",DisplayableExecutionPlan::with_metrics(plan.as_ref()).indent(true))}));
    }
    println!(
        "{}",
        serde_json::to_string(
            &json!({"backend":"direct_datafusion_55.0.0","total_ms":started.elapsed().as_secs_f64()*1000.0,"stages":stages,"intermediate_storage":"persistent in-process MemTable"})
        )?
    );
    Ok(())
}
