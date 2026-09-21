# Verification: alpha.5

[Home](../README.md) · [Current limits](progress.md) · [Agent contracts](agent-guide.md)

This report separates deterministic correctness, local timings, an optional real
agent experiment, and native artifact provenance. Engineering tests make no model
calls. The paired experiment explicitly runs an external model. Historical alpha.4
[verification](releases/alpha4-verification.md) and
[release provenance](releases/alpha4-verification.json) remain archived.

## Correctness

The local release build passes **65 integration scenarios**: 30 foundation, 13
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
| Expiration | Tombstones, no reuse suggestions for expired data, explicit recovery errors |

[Executable checks](../tests/integration/alpha5.py). Full traces remain in CI artifacts.
The [alpha.4 → alpha.5 binary upgrade probe](../benchmarks/performance/alpha5/upgrade.json)
checks old exact large integers and a fixed partial checkpoint, derived SQL, and
old-runtime rejection after schema 4 → 5. The upgrade is one-way. Existing schema-3
stores also have a migration path. An old live coordinator keeps its capabilities
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
| 16 files, alpha.5 progressive | 21.22 | 49.18 |
| 16 files, alpha.5 ordinary SQL | 34.26 | 37.60 |
| 1 file / 16 row groups, alpha.5 progressive | 20.18 | 46.20 |
| 1 file / 16 row groups, alpha.5 ordinary SQL | 32.12 | 35.47 |

Alpha.5 progressive final time is **73.3% lower** than the alpha.4 rerun here. The
new default coalesces 16 checkpoints into two; this is a comparison of default
behaviors, **not equal publication frequency**. It also uses one context per job,
projected row-group scans and a bounded footer cache. The first partial covers only
a processed prefix; a partial and complete result are different evidence. SQL is
still faster when only a final answer is useful. A single enormous row group must
finish before its first aggregate checkpoint. No grouping, filtering or estimates.

Raw repetitions, coverage, I/O and binary hashes:
[16-file alpha.5](../benchmarks/performance/alpha5/progressive-16files.json),
[alpha.4 rerun](../benchmarks/performance/alpha5/alpha4-recheck-16files.json),
[single-file alpha.5](../benchmarks/performance/alpha5/progressive-single-file.json).
These are local candidate binaries, not the separately built native CI archives.

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
[Small trial](../benchmarks/performance/alpha5/session-16384.json) ·
[Million-row trial](../benchmarks/performance/alpha5/session-1048576.json).
Alpha.3 preparation's workload-specific eight-follow-up break-even remains
historical evidence; it has not been relabeled as an alpha.5 rerun.

## Real external-agent paired pilot

The experiment is running for this candidate. Both arms use the same model/settings,
16,391 synthetic rows, a persistent Python environment and a persistent backend.
The agent may compose calls and materialize tables in both arms. Two tasks cover
multi-step exploration and discovering/reusing a previous session's materialized
subset. Three repeats alternate arm order. Independent Python integer-cent answers,
failures, elapsed time, model usage and source-scan evidence are retained.

[Harness](../benchmarks/agent_pair.py). This is optional benchmark infrastructure,
not a runtime dependency or a demonstration of broad agent adoption. Emitted tool
bytes and model tokens are distinct measurements. RowTrail source/result byte
counters and DuckDB scanned-row profiles are not directly interchangeable.

## Above-memory regression

The independent sort/export check passes again: 1,048,576 rows, 228,139,988 source
bytes, 32 MiB engine pool, 26 spills, 64 parts (largest 3,363,970 bytes). One local
correctness run: sort + observation 1,212.30 ms; export 310.13 ms; sampled worker
RSS 137,805,824 bytes. Every exported ID matches an integer-key reference. This is
not a repeated timing study or an RSS cap.
[Raw report](../benchmarks/performance/alpha5/resources-macos.json).

## Native distribution

Native Linux/macOS CI and artifact audit are pending for this candidate. No alpha.5
native archive is claimed verified yet. Budgets remain 30,000,000 compressed bytes,
4,500,000 CLI bytes and 125,000,000 runtime bytes. No new dependency was added;
299 declarations/notices remain. Archives retain Apache-2.0 and upstream notices.
Linux requires glibc 2.39+. Packages are unsigned engineering previews.

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
