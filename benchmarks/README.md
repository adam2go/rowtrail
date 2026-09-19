# Measurements

These are deterministic execution experiments, not paired real-Agent trials.
There are zero model calls. No adoption or performance advantage is established.

## Reproduce

```sh
cargo build --release --locked
python3 tests/integration/exploration.py --bin-dir target/release
python3 -m venv /tmp/rowtrail-bench
/tmp/rowtrail-bench/bin/pip install duckdb==1.5.5
/tmp/rowtrail-bench/bin/python benchmarks/compare.py --repeats 5
```

DuckDB is a benchmark-only dependency. Normal builds and runtime do not use it.
The DataFusion baseline is a developer helper in the runtime executable.

## Initial exact exploration baseline

Measured on Apple arm64 macOS with 24 GiB RAM and 14 logical CPUs, release Rust
1.94.0. The deterministic Parquet fixture contains 16,384 rows in four row groups.
Five repeats use a fresh RowTrail workspace, with OS caches left warm. Each
backend executes the same five calculations: original group, materialized filter,
saved group, saved count, and a different original dimension. Decimal/count
answers are checked against DuckDB. S1/S2 correctness is separately checked by
manual values and integer arithmetic.

Times cover preparation, initial materialization, both saved-result branches,
and the final return to original input. Units are milliseconds.

| Backend | Minimum | Median | Maximum |
|---|---:|---:|---:|
| rowtrail | 347.30 | 353.55 | 363.35 |
| duckdb | 9.43 | 12.83 | 25.69 |
| datafusion | 4.90 | 5.47 | 5.60 |

RowTrail pays for CLI invocations, its coordinator/worker protocol, immutable
result files, fsync, SQLite commits, source validation, and bounded observations.
The coordinator reuses successful workers. Both reference engines retain a
persistent session and in-memory intermediate tables. They are allowed to cache;
they are not forced to restart per question. Direct DataFusion's process startup
is excluded from the table, but its process wall time is retained in the JSON.

This workload favors direct engines. RowTrail is substantially slower here.
Durability and process isolation explain some of the difference, but do not
establish that the added cost is worthwhile for an Agent. Earlier local runs
with a worker per job and an unnecessary extra read measured a 448.76 ms RowTrail
median; the current result is a diagnostic observation, not a controlled claim
of general speedup. Broader datasets and paired external Agent trials remain open.

`baseline.json` retains each repeat and RowTrail source/result read and write
counters. Both saved-result branches read zero original-source bytes. M0's larger
spill experiment and initial artifact measurements are in `m0.json`; those
minimal-probe sizes are not the final package sizes. The initial dependency
feature graph is `m0-dependency-features.txt`.

`traces/macos-local.json` contains the 24 release-binary integration checks and
request/response traces with local paths removed. These include failed/rejected
operations and recovery, not only successful queries. CI uploads separate
platform traces and package metadata. Whole-process resource profiling and
Agent interaction-cost distributions are incomplete; engine pool limits must
not be confused with an RSS guarantee.
