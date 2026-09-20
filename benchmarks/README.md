# Measurements

These are deterministic execution experiments with zero model calls. They do
not establish agent adoption or a performance advantage over mature engines.

## Reproduce

```sh
cargo build --release --locked
python3 -m venv /tmp/rowtrail-bench
/tmp/rowtrail-bench/bin/pip install duckdb==1.5.5
/tmp/rowtrail-bench/bin/python benchmarks/compare.py --entry cli --repeats 5
/tmp/rowtrail-bench/bin/python benchmarks/compare.py --entry session --rows 1048576 --repeats 5
python3 benchmarks/paging.py
/tmp/rowtrail-bench/bin/python benchmarks/resources.py
```

DuckDB is only a benchmark/verification dependency. The product does not require
it or Python. Source fixtures are generated locally, never shipped in the package.

## Alpha.2 exploration results

Same Apple arm64 machine, macOS 26.6.2, 24 GiB RAM, 14 logical CPUs, Rust 1.94.0
release. Five repetitions per entry/scale; no concurrent local build or test
workload. OS caches are left warm. Each repeat has a fresh RowTrail workspace
and fresh persistent reference-engine sessions. Measurements cover opening,
initial materialization, two saved-result branches and return to a different
original dimension. CLI runs include process startup for every operation;
NDJSON includes starting its one process. All answers match independent DuckDB.

Median milliseconds for the same five-query exploration:

| Entry | 16,384 rows | 1,048,576 rows |
|---|---:|---:|
| Alpha.1, CLI per operation | 349.58 | 12,917.72 |
| Alpha.2, CLI per operation | 123.20 | 377.35 |
| Alpha.2, persistent NDJSON | 104.13 | 360.55 |
| DuckDB 1.5.5, persistent session (alpha.2 CLI run) | 9.49 | 90.50 |
| Direct DataFusion 55.0.0, persistent session (same run) | 4.79 | 57.55 |

Holding the CLI entry constant, alpha.2 is about **2.84× / 34.23× faster than
alpha.1 on these two workloads**. The session entry removes further process and
handshake cost. This is not a claim of generally outperforming DuckDB/DataFusion:
RowTrail still pays for durable results, process isolation and protocol. Reference
engines are allowed in-memory intermediate tables. Direct DataFusion process
startup is excluded from its total; process wall time is retained separately.

The million-row filter now writes nine result files in the representative run,
instead of roughly a thousand small batch files. Each saved-result branch opens
28 data requests rather than 3,073, and still reads **zero original-source bytes**.
Original-source reads are unchanged. Files remain sealed, checksummed and durable
before publication; SQLite remains in FULL synchronous mode. Worker metrics split
planning, result writing and acknowledgement, while coordinator metrics report
publication time and part count. Publication is contained in acknowledgement time;
do not add overlapping measurements as if they were independent stages.

[performance/summary.json](performance/summary.json) identifies source commits,
conditions and min/median/max distributions. The adjacent raw reports retain all
repeats, stages, plans, I/O counters and executable SHA-256 hashes. The benchmark
harness accepts CLI exit code 3 for an unfinished wait; it does not mistake an
accepted background job for a failed tool call.

## Bounded paging

A fixed sorted result of 16,384 Int64 IDs, same CLI entry, five warm-cache reads
at each requested page size. Each returned ID is checked independently.

| Returned rows | Alpha.1 median ms | Alpha.2 median ms |
|---|---:|---:|
| 100 | 5.31 | 3.51 |
| 1,000 | 12.33 | 5.00 |
| 10,000 | 570.25 | 21.37 |

The 10,000-row page improves about 26.68× locally after removing repeated
serialization of all prior rows. Each touched part is verified and decoded from
the same bounded byte buffer. Byte budgets, revision-fixed cursors and exact
64-bit integer JSON strings remain unchanged. Raw samples are
[before-paging.json](performance/before-paging.json) and
[after-paging.json](performance/after-paging.json).

## Real out-of-core execution

[resources.json](performance/resources.json) runs a real coordinator/worker query,
not just the direct-engine probe. It sorts 1,048,576 rows (228,139,988 source bytes)
by payload and ID under a **32 MiB engine pool**, with a 512 MiB spill allowance.
The final local run completes in about 1.39 s, reports 26 spill operations, and
publishes 64 parts (largest 3,363,970 bytes). Parquet export takes about 0.51 s.
Every exported ID matches an independent Python integer-key sort; DuckDB only
reads the exported Parquet during this verification.

Sampled worker RSS is about 146 MB. The pool is **not** an RSS hard limit, and
`ps` sampling may miss the true peak. CI runs the same case on macOS and Linux;
its raw platform reports are uploaded separately. Above-memory coverage is still
a limited workload matrix, not a guarantee for every join/aggregate/SQL plan.

## Distribution and regressions

The local alpha.2 CLI is about 3.57 MB and the runtime about 99.7 MB. Native xz
level-6 archives are about 19 MB on this Mac, versus about 35 MB for the previous
gzip package. Compression changes download size, not installed binary size or
runtime speed. End users need neither Rust nor Python/Node/Docker/another engine.
Use the release asset metadata for exact cross-platform sizes and checksums.

[budgets.json](budgets.json) enforces native binary/archive growth limits during
packaging. Integration tests bound parts, check 10,000-row paging and verify zero
original reads for saved-result branches. Shared CI runners do not enforce a
fragile universal millisecond limit. Keep raw repeated measurements on a named
reference machine before making performance claims.

Initial M0 spill, artifact and dependency measurements remain in `m0.json` and
`m0-dependency-features.txt`. `baseline.json` and `client-startup-macos.json` are
historical alpha.1 measurements, not current alpha.2 artifact sizes. The checked-in
integration trace is sanitized and large page arrays are shortened with explicit
recording annotations and hashes; CI uploads full platform verification reports.
