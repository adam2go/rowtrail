# Verification: alpha.8

[Home](../README.md) · [Agent contracts](agent-guide.md) · [Current limits](progress.md)

Alpha.8 improves large-result persistence, resource-aware SQL planning and saved
work discovery. This report separates backend timings, correctness, actual native
artifacts and agent-level evidence. [Alpha.7 report](releases/alpha7-verification.md)
and [provenance](releases/alpha7-verification.json) are archived.

## Correctness and upgrade

The local build and both native build platforms pass **99 integration scenarios**:
30 foundation, 13 alpha.3, ten alpha.4, twelve alpha.5, eight alpha.6, fifteen
alpha.7 and eleven alpha.8. **Nine Rust tests** include twelve subprocess
publication-crash cases and a deterministic broken-ACK regression. Formatting,
Clippy, dependency boundaries, MCP/session, Rust SDK and benchmark-profile checks
pass. [Local final-binary check inventory](../benchmarks/performance/alpha8/verification.json).
[Native CI](https://github.com/adam2go/rowtrail/actions/runs/35691929567)
uses source `0eab3d8a42f89ba9d650225cf64794e1503680c8`.

The [new checks](../tests/integration/alpha8.py) cover exact labels, duplicate-label
pagination, refresh, Unicode/byte bounds, tombstones, idempotency, 1/2/4-partition
aggregates and joins, independently checked full-row sorting, shared scan limits
and actual cancellation. The installed macOS CI archive passes the eleven new
scenarios again on the local Mac, plus its bundled demo and a real upgrade.

A later candidate failed the Linux cancellation race check: the worker was
confirmed stopped, but the final `cancelled` state retained `WORKER_LOST` from
EOF. The added real-child regression fails before the fix and covers unexpected
exit, cancellation and timeout afterward. The final metadata transaction now
chooses state and error from the same durable stop reason. The candidate failure
and earlier timing series are retained in the [experiment record](../benchmarks/performance/alpha8/experiments/).

**Schema 7 is a one-way upgrade from 3/4/5/6.** The new indexed labels and persisted
query options preserve existing fixed revisions and prefix quality. A probe using
published alpha.7 and alpha.8 native archives verifies an old exact result, an old
partial checkpoint, a new query on old data and the old runtime's explicit refusal
to reopen schema 7. [Native upgrade](../benchmarks/performance/alpha8/upgrade-native-macos.json)
· [Local two-build probe](../benchmarks/performance/alpha8/upgrade-local.json).
Stop the coordinator and keep a full pre-upgrade workspace copy for rollback.

## Method and complete exploration

One Apple arm64 Mac, macOS 26.6.2, 24 GiB RAM, 14 logical CPUs, Rust 1.94.0 release.
Each comparison runs serially with alternating version order, fresh workspaces
and identical deterministic input bytes. OS caches are not flushed. There are no
concurrent local builds or timing workloads. Raw files retain every sample and
executable hash; timings are local observations, not universal guarantees.

Five-query exploration includes CLI/session/coordinator startup, opening input,
regional aggregation, saving non-null rows, two saved-result branches and a return
to the original product dimension. Seven trials per version in each series:

| Complete workflow, median ms | alpha.7 | alpha.8 |
|---|---:|---:|
| 16,384 rows, defaults | 37.35 | 37.23 |
| 1,048,576 rows, defaults | 234.26 | **144.22** |
| 1,048,576 rows, both pinned to one partition | 234.39 | **193.83** |

Default million-row time is **38.4% lower**. In a separate alternating series
with one target partition throughout, it is **17.3% lower**. Small data is
essentially unchanged in this sample. Auto planning targets two
partitions for large original scans under the 128 MiB pool, one for small saved
inputs. The 32 MiB pool remains serial. Explicit multi-partition requests require
64 MiB per partition; the target is not a thread or RSS cap.

Persistent DuckDB 1.5.5 and direct DataFusion 55.0.0 keep intermediates in memory.
For each automatic trial, controls receive the largest selected RowTrail target
across its workflow: two in the alpha.8 million-row arm, one in alpha.7 and the
small-input arms. Controls keep that target throughout; RowTrail may use fewer
partitions for its saved data. DuckDB threads and DataFusion partition targets
are not identical hard CPU caps. Direct controls use their default memory limits,
not RowTrail's pool, and direct DataFusion excludes process startup (also recorded
separately). These are strong direct-engine controls, not equal durability systems.

In the alpha.8 million-row default arm, persistent DuckDB takes **70.56 ms** and
direct DataFusion **38.00 ms**: both remain faster. RowTrail additionally pays for
durable admission/results, verification, isolation and protocol. RowTrail's
returned values are checked against DuckDB; the direct DataFusion timing harness
records stage row counts rather than separately serializing answer values.
Independent Python integer/Decimal oracles cover the reuse, projection and sort
workloads below, as well as integration scenarios.

![Complete exploration and low-memory sort, alpha.7 versus alpha.8. Axes start at zero; conditions and negative cases are included in this report.](../benchmarks/performance/alpha8/performance.svg)

[16K raw](../benchmarks/performance/alpha8/exploration-16384.json) ·
[1M defaults](../benchmarks/performance/alpha8/exploration-1048576.json) ·
[1M one partition](../benchmarks/performance/alpha8/exploration-serial-1048576.json).

## Persist and reuse larger results

Part rotation now counts encoded IPC bytes already written plus conservative
next-batch memory headroom. Compressible output needs fewer parts/commits; written
batches are released. The **8 MiB encoded hard limit**, 128-batch bound, 50 ms flush,
immediate first available preview, file/directory sync, checksums and SQLite FULL
commit-before-ACK ordering remain. Prepared Parquet keeps its previous target.
[Design](decisions/008-bounded-parallelism-and-handoff.md).

Five alternating real million-row sorts, each with a **32 MiB engine pool**, spill
and an independent check of every exported ID:

| Measurement | alpha.7 | alpha.8 |
|---|---:|---:|
| Sort median, ms | 912.20 | **462.41** |
| Export median, ms | 258.83 | 250.68 |
| Result parts, median | 43 | **3** |
| Spill count, median | 26 | 26 |
| Largest result part, bytes | 305,114 | 4,655,714 |
| Sampled worker RSS, median bytes | 141,279,232 | 138,149,888 |

Sort time is **49.3% lower**. Timing includes bounded polling, protocol and `ps`
sampling, not just engine CPU. MemoryPool is not an RSS cap; samples can miss true
peaks. The 228,139,988-byte Parquet fixture and each binary are hashed. Python's
independent integer key specifies every sorted row; DuckDB only reads the exported
Parquet for comparison. [All ten runs](../benchmarks/performance/alpha8/resources.json).

A separate **2,097,152-row** materialization followed by three logical saved scans
runs seven alternating trials. Full workflow median **386.52 → 210.68 ms**;
rescan median **56.98 → 53.59 ms**. Parts fall from 17 to four. Alpha.8 stores and
reads 9,282,856 result bytes, with **zero original-source bytes during reuse** and
6,964,366 retained cache bytes at peak, within the existing 8 MiB budget. This
access pattern reads each part once; other patterns can evict and reread. The SQL
can be hand-fused into a single conditional aggregate; this tests repeated-plan
reuse rather than optimal SQL. [All samples and exact oracle](../benchmarks/performance/alpha8/rescan-2097152.json).

Wide Parquet projection also retains alpha.7's range-I/O gain: both read
**1,574,716 source bytes** when projecting two distant columns from a 32-column,
65,536-row high-entropy fixture. Query median **9.55 → 8.75 ms**, open plus query
29.91 → 29.22 ms in seven alternating trials. This small local gain depends on
layout. [Projection records](../benchmarks/performance/alpha8/projection.json).

## Small calls, handoff and retained history

Twenty-one alternating sessions per version, thirty scalar queries each, preserve
an ID above 2^53. Startup and the first query are separate; **609 warmed queries**
share their session/worker. Every sample is retained.

| Median ms (P95 where noted) | alpha.7 | alpha.8 |
|---|---:|---:|
| CLI/coordinator startup | 18.31 | 18.36 |
| First query | 5.92 | 5.90 |
| Warm query | 1.08 | 1.06 |
| Warm query P95 | 3.11 | 1.62 |

The fast small-call behavior is retained; no new cold-start or median scalar gain
is established. The warm P95 improves in this series, with an alpha.7 tail
outlier; this is not an independent tail-latency guarantee. P95 is nearest-rank over observed warm samples, not an SLA or
609 independent cold runs. [Latency records](../benchmarks/performance/alpha8/latency.json).

One retained workspace creates **5,000 labeled exact scalar results**, checking
every answer. After coordinator restart, 31 exact-label catalog lookups have
median **0.54 ms**, P95 **0.66 ms**, including protocol and JSON but excluding
startup. The label index is verified in the SQLite query plan. Early/late labeled
query windows have medians 0.878 / 0.880 ms. There is no GC; file lengths including
SQLite/WAL total 40,928,464 bytes. This is a single history-growth probe, not a
repeated version comparison or general scaling bound. [All samples](../benchmarks/performance/alpha8/catalog.json).

## Regressions and limits

Whole-part verification is preserved. Larger parts therefore make a small page
from a **large saved result** slower. Twenty-one alternating reads of a sorted
million-row id/region/amount result, projecting only ID:

| Page size, median ms | alpha.7 | alpha.8 |
|---|---:|---:|
| 100 rows | 0.64 | **2.00** |
| 1,000 rows | 0.99 | **2.32** |
| 10,000 rows | 5.48 | **6.80** |

The first 100-row page verifies **736,490 → 4,272,602 bytes**. These are logical
read/checksum bytes, not physical device I/O through the OS page cache. This is a
real regression, accepted for the larger complete-workflow/persistence gains;
projected integrity reads remain a performance priority. [Large-result pages](../benchmarks/performance/alpha8/paging-1048576.json).

Small 16K ID-only result paging is essentially unchanged: 100 / 1K / 10K medians
are 0.34 / 0.68 / 5.16 ms before and 0.28 / 0.67 / 5.25 ms after.
[Small-result pages](../benchmarks/performance/alpha8/paging-16384.json).
A 131,072-row high-entropy integer materialization with three exact saved aggregates
is **98.95 → 97.39 ms** (seven alternating trials), about 1.6% faster in this sample.
Compression and part sizing are not universal wins. [Entropy records](../benchmarks/performance/alpha8/entropy.json).

[Exploratory trials](../benchmarks/performance/alpha8/experiments/) retain the
parallel-only and encoded-part candidates, including negative page/entropy cases.
Older rejected four-partition low-memory and startup-polling experiments remain
in the [alpha.7 report](releases/alpha7-verification.md).

## Agent workflow evidence

The bundled [zero-download demo](../examples/quickstart.py) generates 20,003 CSV
orders. Four exact region totals select east; its **385** saved refund rows return
three channel totals. A fresh connection finds the labeled result in one catalog
call and queries it in one more call, with zero original-source data bytes and no
schema-probing loop. Independent Python integers check region/channel/handoff
answers, including ID `9007199254740993`.
[Installed native demo record](../benchmarks/performance/alpha8/demo-native-macos.json).

This proves the deterministic composition path, not a new independent paired-agent
latency/token result. The latest paired pilot remains [alpha.6](releases/alpha6-verification.md#real-external-agent-paired-pilot):
12/12 answers correct, but more time and cumulative input tokens than persistent
DuckDB. New helpers, labels and faster backend timings do not establish a model-
level win. We invite independently reproduced real-agent tasks.

## Native distribution

The verified CI run builds on **Ubuntu 22.04 x86_64** and **macOS 14 arm64**. It
runs all 99 integration scenarios and nine Rust tests on each. The **same Linux
archive** then passes checksum installation, its bundled demo and platform
preflight checks on **Ubuntu 24.04**. No local archive replaces a CI artifact.

| Native release | Download, bytes (MB) | CLI, bytes | Runtime, bytes |
|---|---:|---:|---:|
| macOS arm64 | 19,229,952 (19.23) | 3,733,632 | 100,100,304 |
| Linux x86_64 | 22,556,188 (22.56) | 4,185,696 | 114,908,680 |

All MB are decimal. Unchanged budgets: **30,000,000** per archive, **4,500,000**
per CLI and **125,000,000** per runtime. Cargo.lock changes only the four workspace
package versions; there is **no new external dependency**. End users need no
Rust, Python, Node, Docker, external database or model API key. Python is optional
for the bundled stdlib example and required only for development benchmarks.

Independent archive verification checks SHA-256 files, ELF/Mach-O architecture,
executable sizes, the hashes recorded by native tests, bundled guides/client/demo
and **557 dependency notice files per archive**. The installed macOS CI package
passes the new scenarios and upgrade probe again. The installer rejects glibc
older than 2.35 and musl before downloading, then checks actual executable
compatibility and CLI version before changing the current installation. Checksum-
valid but incompatible or wrong-version packages leave existing links unchanged. Packages remain unsigned engineering
previews; Windows, Linux arm64 and macOS x86_64 are not published targets.

[Exact provenance, hashes and platform reports](release-verification.json) ·
[Reusable archive verifier](../scripts/verify_release.py).
Artifacts and bundled documents come from the verified build source; final report
and README updates on the release tag do not change those executable bytes.

## Reproduce

Build with Rust 1.94.0 and Cargo.lock; use `scripts/cargo-local.sh` for the local
macOS Command Line Tools selection only. [Build and integration commands](../docs/usage.md#build)
· [Benchmark recipes](../benchmarks/README.md#alpha8-release-matrix).

Final local benchmark binaries:

- CLI: `342b33d232b5d30dd242c0659967e5828ff00d0e034db2054a080b64d7ea1267`
- Runtime: `8122e915fbae80b02f236adb9c95c8c9d88774447a65447ce20d64656da59edb`

Native archive hashes differ and are recorded separately. Keep binary provenance,
source/result I/O, returned-value checks and cache/process conditions together
when reproducing a claim.
