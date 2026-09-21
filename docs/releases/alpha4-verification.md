# Verification: alpha.4

[Home](../../README.md) · [Current limits](alpha4-progress.md) · [Agent contracts](../agent-guide.md)

This report separates correctness, local timing and native artifact verification.
No tests call a model. Historical results remain in the
[alpha.3 report](alpha3-verification.md) and
[alpha.3 release provenance](alpha3-verification.json).

## Correctness

The local release build and both native CI platforms pass **53 integration scenarios**: 30 foundation, 13
alpha.3 profile/preparation/storage scenarios, and ten new progressive scenarios.
Six Rust tests include the existing eight subprocess crash-at-commit cases for
Arrow results and prepared Parquet, plus their child harness. MCP/NDJSON and the
old-to-new binary upgrade probe also pass.

| New scenario | What is checked |
|---|---|
| Every checkpoint | Exact count, signed/unsigned sums, nulls and six-digit Decimal means against independent Python arithmetic |
| Snapshot replacement | Every revision contains one aggregate row, with fixed file-prefix coverage |
| Reuse and export | Partial checkpoint SQL uses zero original bytes; export/reopen preserves partial quality |
| Preview disabled | Exactly one final checkpoint |
| Durable events | Every published aggregate revision has its `result.ready` event |
| Result budget | Only the last complete file checkpoint remains readable |
| Unsupported requests | Functions, numeric types, missing columns, duplicate aliases and CSV input fail explicitly |
| Empty input | Count zero, null sum/average, typed schema |
| Cancellation/worker crash | Execution stops; earlier checkpoints remain readable and partial |
| Overflow | Checked arithmetic fails explicitly, without replacing a valid prefix with wrapped values |

[Check inventory](../../benchmarks/performance/alpha4/verification.json) ·
[Executable integration tests](../../tests/integration/alpha4.py).
The full trace of each local/CI run is retained as a verification artifact.

A [real alpha.3 → alpha.4 binary probe](../../benchmarks/performance/alpha4/upgrade.json)
opens a schema-3 store, preserves an exact large-integer fixed revision, derives a
new query from it, and verifies that the old runtime refuses the upgraded schema-4
store. The upgrade is one-way. An old coordinator already running in a workspace
keeps its capabilities until it finishes and exits.

The [same upgrade probe using native CI archives](../../benchmarks/performance/alpha4/upgrade-native-macos.json)
also passes on macOS, using the published alpha.3 archive and the verified alpha.4
release candidate. The report records both archive hashes.

## Progressive latency and total cost

1,048,576 rows split into 16 Parquet files, 7,313,314 source bytes. Count(*),
count(amount), sum and mean; Decimal arithmetic. Five serial repeats with alternating
mode order, warm OS cache and a fresh workspace/session each run. Both modes use
RowTrail. Fixture creation and `open` are excluded; worker startup, observations,
scanning, checkpoint writes and final publication are included.

| Mode | First observed result, median ms | Final result, median ms |
|---|---:|---:|
| Ordinary SQL aggregate | 37.27 | 40.63 |
| Progressive file checkpoints | 22.48 | 189.46 |

The first progressive result is only the completed file prefix, not the full-table
answer. Its exact rows are independently verified for its reported file coverage.
The final answers are also checked independently. Polls are 2 ms apart, so observed
availability is an upper bound. The progressive path persists 16 immutable parts
instead of one, accounting for part of its final-time cost. It is useful when early
bounded evidence or cancellation matters; ordinary SQL is preferable when only the
complete answer matters. It is not a universal query-speed improvement.

[All raw repeats, I/O, coverage and binary hashes](../../benchmarks/performance/alpha4/progressive-1048576.json).
The current contract is whole-file scheduling, no filters/grouping/estimates.
Average is Decimal128(38,6), truncated toward zero. Request sum plus count if exact
rational arithmetic is needed. [Arithmetic and snapshot design](../decisions/004-progressive-file-aggregation.md).

## Existing exploration performance

Same persistent-session five-query workload as alpha.3: open, aggregate, persist a
filtered result, branch twice, then query another original dimension. Five serial
repeats on the same macOS 26.6.2 arm64 machine (24 GiB RAM, 14 logical CPUs), warm
cache, fresh workspace. Every answer is checked against the independent engine.

| Version | 16,384 rows, median ms | 1,048,576 rows, median ms |
|---|---:|---:|
| Alpha.3 NDJSON | 103.99 | 322.28 |
| Alpha.4 NDJSON | 104.03 | 319.67 |

The small differences do not establish a meaningful improvement or regression.
Saved branches retain zero original-source reads and full content verification.
Direct persistent DuckDB/DataFusion remain faster; their current timings and the
fairness limits are retained in the raw reports.
[Small trial](../../benchmarks/performance/alpha4/session-16384.json) ·
[Million-row trial](../../benchmarks/performance/alpha4/session-1048576.json).

The alpha.3 preparation experiment remains historical evidence: 271 ms conversion
and a workload-specific break-even after eight follow-ups, including conversion.
It has not been relabeled as an alpha.4 rerun.

## Above-memory regression

The full independent sort/export check passes again: 1,048,576 rows, 228,139,988
source bytes, 32 MiB engine pool, 26 spills and 64 result parts, max 3,363,970 bytes.
One local correctness run: sort with observation 1,289.69 ms; export 309.93 ms;
sampled worker RSS 139,444,224 bytes. Every exported ID matches the integer-key
reference. This is not a repeated timing study or a process-memory hard limit.
[Raw report](../../benchmarks/performance/alpha4/resources-macos.json).

## Native distribution

[Native CI passed on macOS arm64 and Ubuntu 24.04 x86_64](https://github.com/adam2go/rowtrail/actions/runs/35526103291)
for source commit `9457d672ab34f464fd4fe973b2d15e93dd9c2e82`.
Both archives were downloaded and independently checked against their SHA-256
files and metadata. The installed macOS CI archive ran the progressive example
over eight files: 24 rows, 16 non-null values, sum −36 and mean −2.250000, with
complete final coverage. Installed-package upgrade from alpha.3 also passed.

| Platform | Compressed bytes | CLI bytes | Runtime bytes |
|---|---:|---:|---:|
| macOS arm64 | 19,101,872 | 3,651,040 | 99,884,432 |
| Linux x86_64 | 22,355,504 | 4,096,992 | 114,596,704 |

Budgets remain 30,000,000 compressed bytes, 4,500,000 CLI bytes and
125,000,000 runtime bytes. This iteration adds no dependency beyond alpha.3's
299 declarations/notices. Apache-2.0 and upstream notices remain in each archive.
Linux requires glibc 2.39+. Packages are unsigned engineering previews.

The completed CI run, downloaded checksums, native sizes and platform resource
reports are recorded in [release-verification.json](alpha4-verification.json).
The release tag includes subsequent documentation/provenance updates; executable
sources and the installer match the verified CI commit. Prior published artifacts
remain in the alpha.3 archive above.

## Reproduce

```sh
cargo fmt --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo build --release --locked
cargo test --release --workspace --locked
python3 tests/integration/exploration.py --bin-dir target/release
python3 tests/integration/alpha3.py
python3 tests/integration/alpha4.py
# Optional benchmark-only environment, duckdb==1.5.5:
python benchmarks/progressive.py
python benchmarks/compare.py --entry session --rows 1048576 --repeats 5
python benchmarks/resources.py
# Actual old/new binaries are needed for the compatibility probe:
python3 scripts/upgrade_probe.py /path/to/alpha3 /path/to/alpha4
```

Use the local Cargo wrapper on macOS when needed. Keep timed work serial and avoid
concurrent builds. Shared-runner CI durations are not comparable benchmark medians.
