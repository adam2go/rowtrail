# Verification: alpha.6

[Home](../README.md) · [Current limits](progress.md) · [Agent contracts](agent-guide.md)

This report separates repeated local timings, correctness, external-agent evidence
and native artifact provenance. Historical [alpha.5 verification](releases/alpha5-verification.md)
and [release provenance](releases/alpha5-verification.json) remain archived.

## Correctness

The local release build passes **73 integration scenarios**: 30 foundation,
13 alpha.3, ten alpha.4, twelve alpha.5 and eight alpha.6. Seven Rust tests include
exact page-byte accounting and eight subprocess publication-crash scenarios.
Formatting, Clippy, dependency boundaries, MCP/session/SDK, installation and
same-size corruption checks pass. [New checks](../tests/integration/alpha6.py).

| Alpha.6 coverage | What is checked |
|---|---|
| Encoded integrity | File size/SHA-256, exact nullable Decimal and large IDs, zero source bytes on reuse |
| Storage budget | Exact encoded size succeeds; one byte less fails without a final-only result |
| Page bounds | Three byte budgets, escaped Unicode projected names, nulls, exact numbers, all cursor rows |
| Small/empty results | Plain IPC retains schema, typed nulls and empty completion |
| Compact projection | Full observation, fixed revision, state and error remain available |
| Wait semantics | Deadline never cancels/replays; transport error propagates; partial failure retains quality |
| Reconnection | Fresh transport reads the same immutable compressed revision |
| Corruption | Same-size same-mtime changes fail read, query and export before decoding |

[Two-version storage probe](../benchmarks/performance/alpha6/storage-compat.json):
alpha.6 reads alpha.5's plain parts and fixed partial revisions; alpha.5 reads and
queries new compressed parts; alpha.6 can then read that derived result. Metadata
stays schema 5. Upgrades from schema 3/4 retain alpha.5's one-way migration rules.

## Complete exploration

One Apple arm64 Mac, macOS 26.6.2, 24 GiB RAM, 14 logical CPUs. Seven serial
repeats per binary, reversing binary order on alternate repeats; fresh workspace,
persistent NDJSON/engine session, identical deterministic Parquet bytes. OS cache
is not flushed. No concurrent local builds or timing workloads.

The workflow opens input, groups by region, saves non-null rows, groups the saved
result, counts positive saved rows, then returns to the source for another dimension.
The DuckDB and direct DataFusion baselines retain intermediate tables in memory.
Correctness is compared across engines; integer/Decimal fixture checks also run
in the integration suite. Direct DataFusion engine time excludes process startup;
its process wall time is retained in the raw reports.

| Backend | 16,384 rows, ms | 1,048,576 rows, ms |
|---|---:|---:|
| alpha.5 rerun | 109.46 | 348.38 |
| alpha.6 | 110.34 | 298.90 |
| Persistent DuckDB 1.5.5 (alpha.6 trial arm) | 10.62 | 92.19 |
| Direct DataFusion 55.0.0 (alpha.6 trial arm) | 4.76 | 59.05 |

Million-row elapsed time drops **14.2%**. Small-input time is essentially unchanged
(+0.8% in this sample). The control engines have nearly equal medians across the
old/new arms. RowTrail still pays for disk durability, verification, process
isolation and protocol; this is not a claim to beat an in-process SQL engine.
[16K raw runs](../benchmarks/performance/alpha6/exploration-16384.json) ·
[1M raw runs](../benchmarks/performance/alpha6/exploration-1048576.json).

| Million-row stage, median ms | alpha.5 | alpha.6 |
|---|---:|---:|
| Original region aggregate | 62.52 | 63.29 |
| Materialize non-null subset | 154.82 | 124.74 |
| Group saved subset | 45.08 | 36.89 |
| Count saved positives | 32.79 | 22.89 |
| Original product aggregate | 33.17 | 32.14 |

The 986,895-row saved subset falls from **33,517,954 to 4,217,972 bytes** (87.4%
less), nine parts to six. Each saved branch reads zero original bytes and verifies
all stored result bytes. Summed per-workflow writer time has medians 97.01 →
85.54 ms; acknowledgement time 82.70 → 67.32 ms. Publication time inside an
acknowledgement is not added a second time. No file sync or SQLite FULL commit
was disabled. [Design and bounds](decisions/006-result-performance.md).

## Compression tradeoffs and difficult input

The [experiment inventory](../benchmarks/performance/alpha6/experiments/README.md)
retains plain, buffered, Zstd-fast, LZ4, Zstd-level-1 and larger-part trials with
binary hashes. Preliminary trials are not interleaved; use the final alternating
matrix above for the headline. Compression-only variants did not deliver the
whole final gain. Tiny rows stay plain IPC; Arrow keeps raw buffers if compression
would expand them. Compression can still cost CPU on difficult data.

A separate seeded Python generator creates 131,072 rows with eight high-entropy
63-bit integer columns. Python computes exact sums; SQL explicitly casts to
Decimal(38,0) before summing to avoid a 64-bit accumulator overflow. DuckDB only
converts the CSV fixture to Parquet outside timing. Materialization plus three
saved aggregates takes **150.03 → 138.75 ms**, seven alternating repeats (7.5%
lower). All sums match Python and all saved branches read zero source bytes.
[Raw high-entropy runs](../benchmarks/performance/alpha6/entropy-reuse.json).

## Paging and progressive observations

Fixed sorted 16,384-row results; five warm-cache CLI reads per size. Each row is
checked against the exact expected ID. This includes CLI startup; it is not an
external-agent experiment. Schemas and quality are retained in every response.

| Returned rows | alpha.5, ms | alpha.6, ms |
|---|---:|---:|
| 100 | 3.82 | 3.53 |
| 1,000 | 5.15 | 3.83 |
| 10,000 | 21.92 | 8.80 |

The 10K-row page is **59.8% faster**. [Old reads](../benchmarks/performance/alpha6/paging-alpha5.json) ·
[new reads](../benchmarks/performance/alpha6/paging-alpha6.json).

A one-file, 16-row-group million-row aggregate remains stable: progressive first
observation 19.93 → 19.40 ms; final completion 45.89 → 45.32 ms (five repeats).
Ordinary final-only SQL completes in 34.96 → 34.26 ms. Progressive mode is for
earlier complete-prefix evidence; it does not turn a prefix into an estimate.
[Old progressive runs](../benchmarks/performance/alpha6/progressive-alpha5.json) ·
[new progressive runs](../benchmarks/performance/alpha6/progressive-alpha6.json).

## Above-pool execution and memory

A separate real coordinator/worker sorts 1,048,576 rows from a 228,139,988-byte
Parquet input with a **32 MiB engine pool**. Both versions spill 26 times, then
export every ID; Python independently specifies the entire sort order and DuckDB
reads the export. This is one resource-verification run per version, not a repeated
latency study. The text payload contains repeated content and compresses well;
see the high-entropy workload for a different case.

| Resource run | alpha.5 | alpha.6 |
|---|---:|---:|
| Sort wall time, ms | 1,300.86 | 886.18 |
| Export wall time, ms | 319.06 | 256.07 |
| Stored bytes | 215,294,080 | 12,964,230 |
| Parts | 64 | 43 |
| Largest encoded part, bytes | 3,363,970 | 305,114 |
| Sampled worker peak RSS, bytes | 141,246,464 | 139,689,984 |

RSS is sampled with `ps`, not a hard bound or an exact peak. Engine pools exclude
codec buffers, IPC arrays, the writer buffer and the existing single verified-part
cache. No whole-result memory cache was added. [Old resource run](../benchmarks/performance/alpha6/resources-alpha5.json) ·
[new resource run](../benchmarks/performance/alpha6/resources-alpha6.json).

## Real external-agent paired pilot

The alpha.6 pilot is running with the same requested model/settings as alpha.5:
GPT-6 Astra, xhigh, Codex CLI 0.154.0-alpha.6.2, two tasks and three repeats per
backend. Both arms retain a Python environment and may compose calls/materialize
results. RowTrail receives the published minimal bootstrap and optional helpers;
DuckDB remains a persistent connection. This changed integration means comparison
with alpha.5 model latency is observational, not a controlled prompt A/B.

The harness now uses DuckDB profiling coverage ALL and one-row fetch lookahead,
which finalizes scalar results without collecting an unbounded table. Tests cover
aggregate scans, metadata-only counts, materialization, mixed fetch methods and
catalog queries. Incomplete profiles remain unknown, never zero. Scanned rows,
physical bytes and SQL source references are different metrics. Raw reasoning
stays local; public evidence will retain answers, failures, tool traces and usage.
The [official non-interactive CLI documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
describes the JSON event/output-schema execution used by this optional harness.

## Native distribution

Native Linux/macOS CI and artifact audit are pending. Local macOS packaging passes
all size and checksum-installation checks: 19,189,388 compressed bytes, 3,700,656
CLI bytes, 100,017,600 runtime bytes. These local files are not the release assets.
Budgets remain 30,000,000 / 4,500,000 / 125,000,000 bytes respectively. Cargo.lock
adds no external package; 299 dependency declarations/notices remain. Apache-2.0
and upstream notices stay in every archive. [Release status](release-verification.json).

## Reproduce

```sh
scripts/cargo-local.sh build --release --locked
scripts/cargo-local.sh clippy --locked --workspace --all-targets -- -D warnings
scripts/cargo-local.sh test --release --locked --workspace
python3 tests/integration/alpha6.py
python3 scripts/storage_compat_probe.py /path/to/alpha5/bin target/release
python3 -m venv benchmarks/local/venv
benchmarks/local/venv/bin/pip install duckdb==1.5.5
benchmarks/local/venv/bin/python benchmarks/compare_matrix.py --variant alpha5=/path/to/alpha5/bin --variant alpha6=target/release --rows 1048576 --repeats 7 --output benchmarks/local/exploration.json
benchmarks/local/venv/bin/python benchmarks/reuse.py --variant alpha5=/path/to/alpha5/bin --variant alpha6=target/release --repeats 7
python3 benchmarks/paging.py
benchmarks/local/venv/bin/python benchmarks/resources.py
benchmarks/local/venv/bin/python tests/benchmark_harness.py
benchmarks/local/venv/bin/python benchmarks/agent_pair.py --model gpt-6-astra --effort xhigh --repeats 3 --bootstrap compact --output benchmarks/local/new-agent-pair
python3 scripts/package.py
python3 scripts/install_probe.py
```

The agent pilot requires an authenticated external Codex CLI and records every
planned trial without silent retries. It is optional and excluded from native CI.
The product requires neither a model account nor Python/DuckDB.
