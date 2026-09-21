# Verification: alpha.5

[Home](../../README.md) · [Current limits](../progress.md) · [Agent contracts](../agent-guide.md)

This report separates deterministic correctness, local timings, an optional real
agent experiment, and native artifact provenance. Engineering tests make no model
calls. The paired experiment explicitly runs an external model. Historical alpha.4
[verification](alpha4-verification.md) and
[release provenance](alpha4-verification.json) remain archived.

## Correctness

The local release build and both native CI platforms pass **65 integration scenarios**: 30 foundation, 13
alpha.3, ten alpha.4 and twelve alpha.5. Six Rust tests include eight real subprocess
commit-crash cases and their child harness. Formatting, Clippy, dependency boundaries,
MCP/session entry checks and old/new-binary upgrade checks pass locally.

| Alpha.5 scenario | Evidence |
|---|---|
| Setup | Guide/config JSON works without a coordinator or workspace |
| Every row-group prefix | Exact integer/Decimal oracle, including uneven final group |
| Coalescing | First/final snapshots, unchanged scan bytes; preview:none writes once |
| Count-only scan | Empty projection still counts every row |
| Fixed partial reuse | Immutable result, partial quality and zero original-source bytes |
| Budgets | Complete prefix survives result exhaustion; footer scan exhaustion is correctly classified |
| Invalid requests | Unknown fragments and excessive intervals fail explicitly |
| Empty Parquet | Count zero, typed null, complete coverage |
| Managed Parquet | Checksum validation and discovered final fragment totals |
| Cancel/worker loss | Single-file prefixes survive; stopped worker is confirmed |
| Reconnection/pagination | Fixed bindings found by a fresh client; new objects excluded mid-pagination |
| Stored validity | Known-invalid results excluded from readable counts; expiration tombstones and recovery hints |

[Check inventory](../../benchmarks/performance/alpha5/verification.json) ·
[Executable checks](../../tests/integration/alpha5.py). Full traces remain in CI artifacts.
The [alpha.4 → alpha.5 binary upgrade probe](../../benchmarks/performance/alpha5/upgrade.json)
checks old exact large integers and a fixed partial checkpoint, derived SQL, and
old-runtime rejection after schema 4 → 5. The upgrade is one-way. Existing schema-3
stores also [pass the real old/new-binary probe](../../benchmarks/performance/alpha5/upgrade-from-alpha3.json). An old live coordinator keeps its capabilities
until it finishes and exits; use `doctor` to inspect the actual running version.

## Progressive latency and total cost

One Apple arm64 Mac, macOS 26.6.2, 24 GiB RAM, 14 logical CPUs. 1,048,576 rows,
count(*), count(amount), sum and mean. Five serial repeats per mode with alternating
SQL/progressive order, warm OS cache, fresh workspace/session, no concurrent builds.
Fixture creation and open are excluded; worker startup, scans, observation and
checkpoint publication are included. Observations poll every 2 ms, so measured
first-result time is an upper bound on availability. Independent Python integer /
Decimal arithmetic checks all finals and every first observed prefix.

| Input / mode | First observed result, median ms | Final result, median ms |
|---|---:|---:|
| 16 files, alpha.4 progressive rerun | 21.98 | 184.11 |
| 16 files, alpha.5 progressive (default coalescing) | 21.22 | 49.18 |
| 16 files, alpha.5 manifest_file / interval 0 | 24.30 | 190.85 |
| 16 files, alpha.5 ordinary SQL | 34.26 | 37.60 |
| 1 file / 16 row groups, alpha.5 progressive | 20.18 | 46.20 |
| 1 file / 16 row groups, alpha.5 ordinary SQL | 32.12 | 35.47 |

Alpha.5 progressive final time is **73.3% lower** than the alpha.4 rerun here. The
new default coalesces 16 checkpoints into two; this is a comparison of default
behaviors, **not equal publication frequency**. With every file checkpoint retained,
alpha.5 takes 190.85 ms (same run SQL: 42.66 ms), so these samples do not show an
execution-speed gain at equal publication frequency. The default improvement is
mainly fewer durable checkpoint writes. The implementation uses one context per job,
projected row-group scans and a bounded footer cache. The first partial covers only
a processed prefix; a partial and complete result are different evidence. SQL is
still faster when only a final answer is useful. A single enormous row group must
finish before its first aggregate checkpoint. No grouping, filtering or estimates.

Raw repetitions, coverage, I/O and binary hashes:
[16-file alpha.5](../../benchmarks/performance/alpha5/progressive-16files.json),
[alpha.4 rerun](../../benchmarks/performance/alpha5/alpha4-recheck-16files.json),
[single-file alpha.5](../../benchmarks/performance/alpha5/progressive-single-file.json),
[equal-publication control](../../benchmarks/performance/alpha5/progressive-every-file.json).
These local candidate binaries precede final catalog availability fixes; the SQL
and progressive execution paths are unchanged. Their hashes are distinct from
the separately built native CI archives.

## Existing exploration performance

Five-query persistent-session workload: open, aggregate, materialize a filtered
result, branch twice, then query a different original dimension. Five repeats with
fresh workspaces and warm cache; every answer checked independently.

| Engine / entry | 16,384 rows, median ms | 1,048,576 rows, median ms |
|---|---:|---:|
| Alpha.4 NDJSON (historical) | 104.03 | 319.67 |
| Alpha.5 NDJSON | 104.35 | 318.97 |
| DuckDB 1.5.5 persistent | 10.82 | 88.98 |
| Direct DataFusion 55 persistent | 4.78 | 55.14 |

There is no material ordinary-exploration improvement or regression in these
samples. Direct engines remain faster; RowTrail includes persistence, isolation and
protocol. Baselines retain in-memory intermediates; DataFusion startup is excluded.
Saved-result branches read zero original-source bytes and verify stored content.
[Small trial](../../benchmarks/performance/alpha5/session-16384.json) ·
[Million-row trial](../../benchmarks/performance/alpha5/session-1048576.json).
Alpha.3 preparation's workload-specific eight-follow-up break-even remains
historical evidence; it has not been relabeled as an alpha.5 rerun.

## Real external-agent paired pilot

All **12 planned trials passed** (two tasks × two arms × three repeats), using
Codex CLI 0.154.0-alpha.6.2 with requested model `gpt-6-astra`, effort `xhigh`.
The input has 16,391 synthetic rows. Each arm gets a fresh agent and a persistent
Python environment/backend, may compose calls and materialize intermediates;
arm order alternates. Handoff seeds the same filtered subset before the agent
starts and provides no object/table name. Python integer-cent oracles check every
answer, including IDs above 2^53. No failed trial was removed or retried.

Medians across three repeats per task/arm; total time includes setup:

| Task / backend | Correct | Total seconds | Code calls | Bridge output bytes | Cumulative input tokens (cached) |
|---|---:|---:|---:|---:|---:|
| Explore / RowTrail | 3/3 | 96.63 | 6 | 6,229 | 181,085 (155,264) |
| Explore / persistent DuckDB | 3/3 | 56.73 | 3 | 938 | 75,999 (58,752) |
| Handoff / RowTrail | 3/3 | 55.49 | 3 | 5,729 | 113,009 (75,776) |
| Handoff / persistent DuckDB | 3/3 | 45.52 | 3 | 606 | 74,453 (43,392) |

**This pilot does not show a RowTrail efficiency advantage.** It demonstrates
correctness and usable saved-result discovery. Both backends complete all handoffs
without scanning the original input. RowTrail exploration has 4/3/3 source-reading
jobs, versus 3/3/3 source-reading SQL statements for DuckDB. These logical operation
counts are not identical physical scan measures. RowTrail reports 3,018,500 /
2,342,056 / 2,342,056 original-source bytes for exploration and zero for handoff
(excludes metadata reads during open). DuckDB profiles are missing for some
fetchone/metadata statements; complete exploration scan-row counts are **unknown**,
not zero. Handoff statements and profiles show saved-table reuse; all traces were
reviewed. Do not compare DuckDB scanned rows with RowTrail physical bytes.

Cumulative input tokens sum every model input, including cached tokens and CLI
system/tool scaffolding; they are not peak context size or billed uncached tokens.
Bridge bytes exclude separate guide/schema reads. The model already knows SQL;
RowTrail supplies a longer guide/schema and exposes more job/quality metadata.
Model scheduling/network/cache variation contributes to total time. Backend code
itself has median 81.58 / 9.76 ms (explore) and 24.00 / 6.47 ms (handoff), much less
than end-to-end agent time. Three repeats and tiny synthetic tasks support only
descriptive comparisons; progressive early stopping and real customer workflows
are not evaluated. The next integration priority is less discovery text, fewer
irrelevant fields and fewer tool round trips while keeping quality explicit.

[Full sanitized traces, answers and usage](../../benchmarks/performance/alpha5/agent-pair/report.json) ·
[Summary and measurement limits](../../benchmarks/performance/alpha5/agent-pair/summary.json) ·
[Harness](../../benchmarks/agent_pair.py). Raw model reasoning is excluded from published
records. The external harness consumes model quota; the product still makes no
model calls and needs no provider account.

## Above-memory regression

The independent sort/export check passes again: 1,048,576 rows, 228,139,988 source
bytes, 32 MiB engine pool, 26 spills, 64 parts (largest 3,363,970 bytes). One local
correctness run: sort + observation 1,212.30 ms; export 310.13 ms; sampled worker
RSS 137,805,824 bytes. Every exported ID matches an integer-key reference. This is
not a repeated timing study or an RSS cap.
[Raw report](../../benchmarks/performance/alpha5/resources-macos.json).

## Native distribution

[The Linux x86_64 / macOS arm64 CI matrix](https://github.com/adam2go/rowtrail/actions/runs/35553205162) passed at source commit
`dedf04fff543655b4ff44e8ea9b627796d738a32`. Both archives were downloaded, hashed independently and checked against
metadata/checksum files, binary size budgets and license contents. The actual macOS
archive was installed: guide/config start no runtime, a 16,391-row / five-row-group
example completes with exact count/sum/mean, and a fresh session discovers its fixed
result and reads it back. [Published alpha.4 → native alpha.5 upgrade](../../benchmarks/performance/alpha5/upgrade-native-macos.json)
also preserves old fixed and partial revisions and rejects the old runtime afterward.

| Platform | Compressed bytes | CLI bytes | Runtime bytes |
|---|---:|---:|---:|
| macOS arm64 | 19,086,900 | 3,700,672 | 99,984,384 |
| Linux x86_64 | 22,422,336 | 4,134,696 | 114,765,344 |

Budgets remain 30,000,000 compressed bytes, 4,500,000 CLI bytes and 125,000,000
runtime bytes. No new dependency was added; 299 declarations/notices remain.
Archives retain Apache-2.0 and upstream notices. Linux requires glibc 2.39+.
Packages are unsigned engineering previews. [Exact provenance and platform resource checks](alpha5-verification.json).
The tag includes later documentation/measurement updates; executable sources,
installer and CI checks match the verified source commit. Bundled documentation
is the CI-time candidate snapshot; the repository carries the completed report.

## Reproduce

```sh
cargo fmt --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo build --release --locked
cargo test --release --workspace --locked
python3 tests/integration/exploration.py --bin-dir target/release
python3 tests/integration/alpha3.py
python3 tests/integration/alpha4.py
python3 tests/integration/alpha5.py
# Optional benchmark-only environment, duckdb==1.5.5:
python benchmarks/progressive.py
python benchmarks/progressive.py --files 1 --row-group-size 65536
python benchmarks/compare.py --entry session --rows 1048576 --repeats 5
python benchmarks/resources.py
python3 scripts/upgrade_probe.py /path/to/alpha4 /path/to/alpha5 --old-schema 4
# Optional authenticated external Codex CLI; consumes model quota:
python benchmarks/agent_pair.py --model gpt-6-astra --effort xhigh --repeats 3
```

Use the local Cargo wrapper on macOS when needed. Keep timed work serial and avoid
concurrent builds. Shared-runner CI durations are not comparable benchmark medians.
