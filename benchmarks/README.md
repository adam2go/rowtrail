# Measurements

Core performance and correctness experiments make zero model calls. The optional
`agent_pair.py` harness runs an external model and reports its usage separately.
Neither kind establishes broad agent adoption or universal engine superiority.

For a readable inventory of all release checks, platform results, artifact sizes
and checksums, start with the [verification report](../docs/verification.md).

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

## Alpha.3: integrity, profiles and explicit preparation

[The current report](../docs/verification.md) publishes full-exploration measurements
with content verification on saved-result SQL and export, including the initial
software-hash regression and subsequent runtime-detected CPU acceleration.
Raw five-repeat reports and executable hashes are in [performance/alpha3/](performance/alpha3/).

`prepare.py` separately compares typed CSV with explicit managed-Parquet conversion.
It includes startup, open, conversion, one profile and ten subsequent queries;
mode order alternates and a Python integer-cent oracle checks all answers. The
measured break-even is workload-specific and is never used as an automatic policy.
Product code does not depend on DuckDB; this harness uses it only to write fixtures.

## Alpha.4: useful partial latency versus final work

`progressive.py` compares ordinary SQL and whole-file progressive aggregation on
16 files / 1,048,576 rows. It measures observed first-result time separately from
final completion and retains file-prefix quality, physical I/O and every raw run.
Every observed partial and final answer matches a Python integer/Decimal oracle.
The first partial arrives earlier, while retaining 16 checkpoints makes final
completion slower. Read the [current report](../docs/verification.md) before using
these measurements; a partial and complete result are different evidence.

## Alpha.5: row groups, reconnection and real agents

`progressive.py` now records fragment/checkpoint settings and supports single-file
row-group workloads. The alpha.4 rerun and alpha.5 defaults have different checkpoint
frequencies; read the [current verification](../docs/verification.md) before comparing.

`agent_pair.py` runs the same externally authenticated Codex model against RowTrail
and a persistent DuckDB connection, with persistent Python variables on both sides.
It retains all paired trials, independent answers, tool traces, token usage and
separate source-scan evidence. It is never imported by the product. Raw Codex events
stay under ignored `benchmarks/local/`; published records exclude reasoning events
and replace local paths. Running it consumes model quota. No automatic retry or
selection of only successful trials.

## Alpha.7: durable latency, narrow reads and code composition

Final raw data is in [performance/alpha7/](performance/alpha7/); the
[report](../docs/verification.md) includes methods, outliers, limits and rejected
experiments. Keep old/new executable pairs in separate directories and run serially:

```sh
python3 benchmarks/latency.py --variant alpha6=/path/alpha6 --variant alpha7=target/release --repeats 21 --warm-queries 30
/tmp/rowtrail-bench/bin/python benchmarks/compare_matrix.py --variant alpha6=/path/alpha6 --variant alpha7=target/release --rows 1048576 --repeats 7 --output benchmarks/local/exploration-alpha7.json
/tmp/rowtrail-bench/bin/python benchmarks/rescan.py --variant alpha6=/path/alpha6 --variant alpha7=target/release --repeats 7
/tmp/rowtrail-bench/bin/python benchmarks/projection.py --variant alpha6=/path/alpha6 --variant alpha7=target/release --repeats 7
/tmp/rowtrail-bench/bin/python benchmarks/reuse.py --variant alpha6=/path/alpha6 --variant alpha7=target/release --repeats 7
python3 benchmarks/paging_matrix.py --variant alpha6=/path/alpha6 --variant alpha7=target/release --repeats 21
/tmp/rowtrail-bench/bin/python benchmarks/rescan.py --variant alpha7=target/release --rows 2097152 --repeats 1
/tmp/rowtrail-bench/bin/python benchmarks/progressive.py --files 1 --repeats 5
/tmp/rowtrail-bench/bin/python benchmarks/resources.py
```

Add `--output path.json` to retain each report separately. Use `--rows 16384` for
small exploration. Latency reports retain fresh-start/first/warm samples separately;
warmed samples share workers. Projection uses a deliberately wide uncompressed
Parquet layout and an independent Python oracle. Rescan tests three logical
branches, which hand-written SQL could fuse. Paging holds persistent sessions
and alternates versions; its CLI predecessor remains a different timing boundary.
Resource/RSS checks are separate from repeated latency studies. CI does not assert
fragile absolute milliseconds; packaging enforces the existing size budgets.

The manual agent walkthrough is not a new paired-agent comparison. Alpha.6's
external-agent pilot remains the latest measured model-level evidence. Schema 6
is a one-way upgrade, verified by `scripts/upgrade_probe.py OLD NEW --old-schema 5`.

`python3 benchmarks/plot_alpha7.py` renders the final exploration SVG from the
recorded JSON. Matplotlib is an optional documentation dependency only.

`python3 benchmarks/history.py --queries 5000` retains every scalar result in one
workspace and compares early/late warm windows. It is a single-session growth
check, separate from alternating latency comparisons and from quota accounting.

## Alpha.8 release matrix

Use Python 3.11+ for the harnesses; only the benchmark virtual environment needs
`duckdb==1.5.5`. RowTrail has no Python or DuckDB runtime dependency. Old alpha.7
Linux archives require glibc 2.39; compare on a host compatible with both builds.

```sh
python3 -m venv /tmp/rowtrail-bench
/tmp/rowtrail-bench/bin/pip install duckdb==1.5.5
/tmp/rowtrail-bench/bin/python benchmarks/compare_matrix.py --variant alpha7=/path/to/alpha7 --variant alpha8=target/release --rows 1048576 --repeats 7 --output benchmarks/local/exploration-alpha8.json
/tmp/rowtrail-bench/bin/python benchmarks/compare_matrix.py --variant alpha7=/path/to/alpha7 --variant alpha8=target/release --variant-partitions alpha8=1 --rows 1048576 --repeats 7 --output benchmarks/local/exploration-alpha8-serial.json
/tmp/rowtrail-bench/bin/python benchmarks/resource_matrix.py --variant alpha7=/path/to/alpha7 --variant alpha8=target/release --repeats 5
/tmp/rowtrail-bench/bin/python benchmarks/rescan.py --variant alpha7=/path/to/alpha7 --variant alpha8=target/release --rows 2097152 --repeats 7
/tmp/rowtrail-bench/bin/python benchmarks/reuse.py --variant alpha7=/path/to/alpha7 --variant alpha8=target/release --repeats 7
/tmp/rowtrail-bench/bin/python benchmarks/projection.py --variant alpha7=/path/to/alpha7 --variant alpha8=target/release --repeats 7
python3 benchmarks/latency.py --variant alpha7=/path/to/alpha7 --variant alpha8=target/release --repeats 21 --warm-queries 30
python3 benchmarks/paging_matrix.py --variant alpha7=/path/to/alpha7 --variant alpha8=target/release --source-rows 1048576 --wide-result --repeats 21
python3 benchmarks/catalog.py --bin-dir target/release --queries 5000 --lookups 31
```

Run the 16K exploration and ordinary 16K paging variants as well. Keep timing
workloads sequential, without concurrent builds. The full resource matrix checks
every exported sorted ID independently on every trial; its RSS samples are not
a hard bound. A result file larger than the cache is not a promise of rereads:
access order and overlapping streams determine the live working set.

`compare.py` now gives direct controls the largest RowTrail partition target in
that trial (older alpha.7 reports implicitly target one). `--partitions` pins all
engines; `compare_matrix.py --variant-partitions NAME=N` overrides just one variant,
useful for an old binary without the new request field. Direct DuckDB/DataFusion
retain intermediates and default memory allowances; only their thread/partition
targets are matched, not durability or hard CPU limits. RowTrail answer values
are checked against DuckDB. The direct DataFusion reference records row counts
and plan timings, not separately serialized answer values.

The catalog probe retains every labeled result, restarts the coordinator and
measures exact-label lookup plus a final source-free handoff. It is a single
history-growth probe, not a model study. The bundled `examples/quickstart.py`
provides a small deterministic handoff demo with independent answer checks.

[Current measurements](../docs/verification.md) include the small-page regression
from larger checked parts and the high-entropy case. `plot_alpha8.py` renders
checked-in JSON using optional Matplotlib; graph rendering is outside timing.
