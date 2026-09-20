# Verification: alpha.3

[Home](../README.md) · [Contracts](agent-guide.md) · [Current limits](progress.md)

This report separates deterministic correctness, local performance measurements
and release artifact verification. No test calls a model. It is not a real-agent
adoption trial. Alpha.2 evidence remains in [the archived report](releases/alpha2-verification.md)
and [release provenance](releases/alpha2-verification.json).

## Correctness and lifecycle

The local release build passes **43 integration scenarios** (30 existing + 13
new), **six Rust tests**, MCP-to-CLI result consumption, NDJSON lifecycle/frame
bounds, the Rust SDK and the generated preparation/export/GC example.
The [machine-readable check inventory](../benchmarks/performance/alpha3/verification.json)
lists every scenario; the scripts keep complete request/response traces locally.

| Alpha.3 addition | Evidence |
|---|---|
| Selected-column null/min/max | One CSV read; exact large integers/Decimal and null semantics |
| Top-k | Exact counts, deterministic count/value ordering, bounded output |
| Saved-result profiles/head | Explicit revision; zero original-source reads |
| Preparation | CSV/TSV, empty and multi-file tables, Decimal, idempotency, independent copy |
| Failed preparation | Late parse error and time exhaustion publish no usable dataset |
| Integrity | Same-size/same-mtime byte corruption fails read, derived SQL and export |
| Conflicting aliases | External alias cannot bypass managed-Parquet checksum verification |
| Retention | Pins/retained children protect ancestors; active job inputs survive GC |
| GC | Dry-run preserves data; apply gives durable `OBJECT_EXPIRED`; repeated GC is safe |
| Quota | Admission reserves result/spill budgets; completed tasks release reservations |
| SQL | Join plus partitioned window agrees with an independent Python integer oracle |
| Commit crashes | Four subprocess fault points × Arrow/Parquet paths = eight scenarios |

The crash test stops immediately after part rename, after part commit, before
terminal commit and after terminal commit. It checks preview preservation, orphan
cleanup, terminal-state preservation and all-or-nothing prepared-dataset visibility.
Hooks exist only in test builds. Two of the six Rust tests are this subprocess
suite and its child harness. External export/sidecar crash reconciliation remains
future work; these tests do not establish atomicity across all external files.

## Full-exploration performance

One macOS 26.6.2 arm64 machine, 24 GiB RAM, 14 logical CPUs, Rust 1.94.0, release
build, five serial repeats, warm OS cache and fresh workspace per run. Workload:
open → aggregate → persist filtered rows → two saved-result branches → query a
different original dimension. Observations and client entry costs are included.

| Entry | 16,384 rows, median ms | 1,048,576 rows, median ms |
|---|---:|---:|
| Alpha.2 CLI, historical | 123.20 | 377.35 |
| Alpha.3 CLI | 122.92 | 345.68 |
| Alpha.2 NDJSON, historical | 104.13 | 360.55 |
| Alpha.3 NDJSON | 103.99 | 322.28 |
| DuckDB 1.5.5 persistent, current CLI trial | 9.39 | 92.23 |
| DataFusion 55.0.0 persistent, current CLI trial | 4.56 | 58.93 |

Every trial checks answers. Direct engines may retain intermediate tables in
memory; RowTrail persists them. Direct DataFusion excludes process startup but
also records process wall time. The data supports a local improvement over
alpha.2; it does not establish an advantage over these mature engines.

[Raw CLI small](../benchmarks/performance/alpha3/cli-16384.json) ·
[Raw CLI million](../benchmarks/performance/alpha3/cli-1048576.json) ·
[Raw session small](../benchmarks/performance/alpha3/session-16384.json) ·
[Raw session million](../benchmarks/performance/alpha3/session-1048576.json)

Full integrity initially slowed million-row session exploration to 469.98 ms.
Runtime-detected SHA-256 CPU acceleration reduced it to 322.28 ms while retaining
verification on all managed-input paths. The [pre-acceleration repeats](../benchmarks/performance/alpha3/before-sha-acceleration/)
are retained, with their own executable hashes. The bounded cache shares verified
bytes across range reads; it does not skip verification across jobs.

## Preparation includes conversion cost

1,048,576 rows; 32,269,410-byte typed CSV, approximately 8.31 MB managed Parquet.
Five repeats per mode, alternating mode order. Both paths include session startup,
open, amount profile and ten follow-up aggregate queries; the prepared path also
includes conversion. Every answer matches a Python integer-cent oracle. DuckDB
only writes the fixture here, and is not a speed baseline for this experiment.

| Path | Prepare, median ms | Profile, median ms | Complete workflow, median ms |
|---|---:|---:|---:|
| Read CSV directly | 0 | 70.24 | 736.02 |
| Explicit prepare + query | 271.11 | 22.27 | 652.29 |

The first cheaper cumulative prepared result occurs at **eight follow-up queries**
in this fixture. A single question does not justify its conversion cost. Row width,
types, selectivity, cache and question count can change the result. Whole-part hash
verification is included even when a Parquet query projects fewer columns.
[All repeats, stages, I/O, binary hashes and conditions](../benchmarks/performance/alpha3/prepare-1048576.json).

## Above-memory execution

The existing independent full-row check still passes: 1,048,576 rows, 228,139,988
source bytes, 32 MiB engine pool, 26 spill events, 64 result parts (largest
3,363,970 bytes). Local sort including its observation: 1,183.52 ms; export:
310.19 ms. Sampled worker RSS: 142,000,128 bytes. This is one correctness run,
not a repeated timing benchmark or an RSS hard-bound measurement. Every exported
ID matches the independent integer-key sort.
[Raw resource report](../benchmarks/performance/alpha3/resources-macos.json).

## Native distribution

Release candidates must pass the complete macOS arm64 and Ubuntu 24.04 x86_64 CI
matrix before publication. Budgets remain 30,000,000 compressed bytes per archive,
4,500,000 CLI bytes and 125,000,000 runtime bytes. Archives contain both binaries,
Apache-2.0 and upstream notices for 299 dependency declarations. One small SHA
assembly backend was added; no model, service, Python or Node dependency was added.

Alpha.3 artifact hashes and the completed CI run are recorded in
[release-verification.json](release-verification.json) after verification. The
previous published artifact evidence remains in the alpha.2 archive above.

## Reproduce

```sh
cargo build --release --locked
cargo test --release --workspace --locked
python3 tests/integration/exploration.py --bin-dir target/release
python3 tests/integration/alpha3.py
# In an optional benchmark-only environment with duckdb==1.5.5:
python benchmarks/compare.py --entry session --rows 1048576 --repeats 5
python benchmarks/prepare.py --rows 1048576 --repeats 5
python benchmarks/resources.py
```

Raw performance files carry binary SHA-256 hashes. Keep timed work serial and
avoid concurrent builds. Use `scripts/cargo-local.sh` for the local macOS toolchain
selection when needed. CI shared-runner durations are correctness evidence, not
comparable benchmark medians.
