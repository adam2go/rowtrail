# Verification: alpha.7

[Home](../../README.md) · [Agent contracts](../agent-guide.md) · [Current limits](../progress.md)

Alpha.7 reduces durable small-result overhead, repeated verified reads and agent
orchestration. This report separates backend timing, correctness, manual use and
native artifact verification. [Alpha.6 report](alpha6-verification.md) and
[provenance](alpha6-verification.json) are archived.

## Correctness and migration

The final local build and both native CI platforms pass **88 integration scenarios**: 30 foundation,
13 alpha.3, ten alpha.4, twelve alpha.5, eight alpha.6 and fifteen alpha.7.
**Eight Rust tests** include twelve subprocess publication-crash cases and a
deterministic real-pipe regression for worker death before acknowledgement.
Formatting, Clippy, dependency boundaries, MCP/session, the benchmark-profile
guard and the real alpha.6 → alpha.7 upgrade probe pass. Native artifact verification is below.

[Alpha.7 checks](../../tests/integration/alpha7.py) cover inline and mixed storage,
nullable exact values and large integers, SQLite corruption, quota/GC, restart,
three saved scans within one-pass byte budget, bounded source reads, literal file
names, code composition and transport failures without replay. Checks preserve
partial source coverage and reject treating truncated rows as a complete answer.
The real-pipe test preserves a committed revision while marking the lost worker
interrupted and confirming its exit; cancellation and budget finish rules remain.

**Metadata upgrades once to schema 6; old runtimes refuse upgraded workspaces.**
[The two-binary probe](../../benchmarks/performance/alpha7/upgrade-local.json) preserves
old fixed results and a partial checkpoint, then queries an old result with the
new runtime. For rollback, stop the coordinator and copy the full workspace before upgrading.
[Direct schema-3 upgrade](../../benchmarks/performance/alpha7/upgrade-alpha3-local.json)
and [schema-4 upgrade](../../benchmarks/performance/alpha7/upgrade-alpha4-local.json)
also pass with actual old archives; alpha.4's partial checkpoint retains its original
file-prefix semantics. No new external dependency was added. [Design](../decisions/007-agent-workflow-performance.md).

## Complete exploration

One Apple arm64 Mac, macOS 26.6.2, 24 GiB RAM, 14 logical CPUs, Rust 1.94.0 release.
Seven serial, alternating runs per version; fresh workspaces and persistent native
sessions; identical deterministic fixture bytes. OS caches are not flushed. No
concurrent local build or timing workload. Fixture generation and cleanup are
outside timing; session/coordinator startup and open are included.

Five queries: aggregate by region, save a non-null subset, group saved rows, count
saved positives, return to the original product dimension. Persistent DuckDB
1.5.5 and direct DataFusion 55.0.0 retain in-memory intermediate tables. DuckDB uses
one thread; both DataFusion paths target one query partition (runtime/I/O threads
are not an OS one-core limit). Direct DataFusion excludes process startup;
its separate process wall time is retained. All engines agree on answers.

| Backend, median ms | 16,384 rows | 1,048,576 rows |
|---|---:|---:|
| alpha.6 rerun | 110.33 | 296.09 |
| alpha.7 | **36.15** | **239.95** |
| Persistent DuckDB, alpha.7 trial arm | 8.61 | 95.46 |
| Direct DataFusion, alpha.7 trial arm | 4.39 | 63.67 |

Elapsed time is 67.2% / 19.0% lower. Direct engines remain faster; RowTrail also
pays for durable acceptance/results, content verification, isolation and protocol.
Small control-engine times vary between arms (DuckDB 10.46 vs 8.61 ms);
this is a local sample, not universal superiority or an exact causal decomposition.
[16K raw](../../benchmarks/performance/alpha7/exploration-16384.json) ·
[1M raw](../../benchmarks/performance/alpha7/exploration-1048576.json).

## Small durable queries

Twenty-one alternating sessions per version, thirty exact scalar queries each.
The first query starts a worker; the remaining **609 warmed samples** share their
session and worker. Every answer checks an integer above JavaScript's safe range.
P95 is nearest-rank over observed samples, not a service-level guarantee or 609
independent cold runs. CLI protocol transfer and Python decoding are included.

| Measurement, ms | alpha.6 | alpha.7 |
|---|---:|---:|
| Fresh CLI/coordinator startup median | 19.83 | 20.57 |
| First query median | 27.97 | 8.56 |
| Warm query median | 18.12 | **1.19** |
| Warm query P95 | 20.52 | **1.82** |

Warm median time is 93.4% lower. There is **no demonstrated cold-start gain**.
Arrow parts up to 128 KiB now commit their descriptor and checksum-verified BLOB
in one SQLite FULL transaction, avoiding separate small-file durability operations.
Acknowledgement still follows durable commit; large parts retain file sync and
atomic publication. [All samples](../../benchmarks/performance/alpha7/latency.json).

A separate long-session check retains **5,000 exact scalar results** in one
workspace, with no GC. The early warm 100-query window has median/P95
0.850 / 1.237 ms; the last 100 have 0.838 / 1.058 ms. All values are independently
checked and all 5,000 results remain catalogued. File lengths including SQLite/WAL
total 43,306,992 bytes. This single session shows no obvious history slowdown for
this workload; it is not a repeated version comparison or a general scaling bound.
[Every sample](../../benchmarks/performance/alpha7/history-alpha7.json).

## Read less, retain bounded verified bytes

A job-local LRU holds at most **8 MiB encoded bytes / 128 entries**, replacing a
single-part cache under the same byte allowance. Three UNION ALL branches scan a
saved 1,048,576-row result: exact Decimal sums and large IDs match Python. Seven
alternating runs; both versions store 4,655,338 bytes in nine parts.

- Rescan stage: **69.91 → 31.24 ms**.
- Complete materialize-and-rescan workflow: 255.51 → 211.16 ms.
- Saved-result bytes read: **19,605,074 → 4,655,338**; zero original-source bytes.

The SQL can be hand-fused into one conditional aggregate; this fixture tests reuse
inside a repeated-scan plan, not optimal SQL. Each new job rechecks stored bytes.
Cache hits return captured verified bytes; corruption is never hidden across jobs.
[Raw rescans](../../benchmarks/performance/alpha7/rescan.json).

An additional two-million-row check stores 9,290,682 bytes in 17 parts,
above the cache budget; retained cache peaks at 8,163,766 bytes.
Interleaved branches share bytes here; this does not guarantee no rereads under a
worst-case sequential access pattern. In-flight slices and codec/engine buffers
are separate from retained cache entries. [Bound check](../../benchmarks/performance/alpha7/cache-eviction.json).

A deliberately wide Parquet fixture has 65,536 rows, 32 seeded high-entropy Int64
columns and 2,048-row groups. Querying two distant columns reads exact requested
ranges rather than inter-column gaps for range batches up to 8 MiB.

| Wide projection, median | alpha.6 | alpha.7 |
|---|---:|---:|
| Query source bytes | 17,331,136 | **1,574,716** |
| Query time, ms | 31.81 | 9.71 |
| Open plus query, ms | 52.34 | 29.44 |

That is 90.9% fewer query-read bytes for this layout, including query footers but
excluding open's metadata reads. Counters are application reads, not physical
device I/O. A pre-range prototype reads the same 17,331,136 bytes; other prototype
changes prevent attributing its total latency difference solely to ranges.
Managed parts still require whole-part verification. The range batch reserves
bytes before reading; its bounded buffers are outside the engine pool.
[Projection raw](../../benchmarks/performance/alpha7/projection.json).

A separate 131,072-row, eight-column high-entropy fixture materializes and runs
three saved aggregates in **142.14 → 97.90 ms** (seven alternating runs).
Python supplies exact integer sums; SQL casts to Decimal(38,0). Ordinary Int64/
UInt64 SUM retains the engine's overflow semantics; wire precision does not widen
an accumulator. [Raw](../../benchmarks/performance/alpha7/entropy-reuse.json).

## Paging, progressive work and resources

Twenty-one alternating persistent-session reads per size, transfer/JSON parsing
included, materialization and startup excluded; all returned IDs checked exactly.

| Page rows, median ms | alpha.6 | alpha.7 |
|---|---:|---:|
| 100 | 0.374 | 0.346 |
| 1,000 | 0.734 | 0.700 |
| 10,000 | 5.505 | 5.419 |

Reusing the coordinator's SQLite connection removes a small fixed inline-read
cost; 10K pages are essentially unchanged. Earlier sequential CLI samples varied
and did not show a paging gain; they are retained in the experiment inventory.
[Persistent raw and P95](../../benchmarks/performance/alpha7/paging-matrix.json).

A one-file, 16-row-group million-row progressive aggregate (five runs/version,
measured in separate batches) reaches its first prefix in
20.63 → **7.37 ms** and final completion in
46.65 → **22.48 ms**. Ordinary final-only SQL completes in
35.12 → 19.93 ms. Checkpoint coalescing is unchanged at 50 ms;
actual counts depend on completion speed, so these are not equal checkpoint-frequency
runs. Every observed prefix and final result matches Python arithmetic.
[Old](../../benchmarks/performance/alpha7/progressive-alpha6.json) ·
[new](../../benchmarks/performance/alpha7/progressive-alpha7.json).

One resource check per version sorts 1,048,576 rows from 228,139,988 Parquet bytes
under a **32 MiB engine pool** and exports every ID, checked against a Python sort.
Alpha.7 spills 26 times, produces 43 parts (largest 305,114 bytes), and samples
worker RSS of 144,801,792 bytes. Sort/export take 929.08 / 263.47 ms.
The pool is **not an RSS limit**; sampling can miss the true peak. These are resource
checks, not repeated latency evidence. [Old](../../benchmarks/performance/alpha7/resources-alpha6.json) ·
[new](../../benchmarks/performance/alpha7/resources-alpha7.json).

## Agent workflow evidence

The locally printable stdlib client accepts responses as bindings and handles
mechanical waits, preserves full quality/errors, never silently replays a mutation,
and never implicitly collects a table. `rows()` only accepts complete exact final
untruncated observations. [Minimal bootstrap](../agent-quickstart.md).

A manual primary-agent walkthrough used the printed client on 20,003 generated
CSV orders: discovered the actual schema, found the highest refund-value region,
saved 1,531 rows with a zero-row observation, and returned three channel totals.
A fresh process recovered the subset from the catalog and computed exact count,
large-ID minimum and Decimal sum, reading zero original bytes. Python independently
checked the values. **It still needed three metadata-only schema inspections to
identify the opaque catalog result.** Better result descriptions and bounded schema
hints are a useful next improvement. [Walkthrough record](../../benchmarks/performance/alpha7/dogfood.json).

This is manual use, **not a new paired agent evaluation**. The latest actual paired
pilot remains [alpha.6's twelve trials](alpha6-verification.md#real-external-agent-paired-pilot):
all correct, but RowTrail slower and using more cumulative input tokens than
persistent DuckDB. Backend gains and helpers do not prove overall agent latency
or token gains. The product continues to make zero model calls.

## Rejected experiments

Higher query parallelism improved one million-row workflow but failed the same
32 MiB sort; adaptive startup polling had inconsistent benefit. Neither ships.
The [inventory](../../benchmarks/performance/alpha7/experiments/README.md) retains
failed, negative and intermediate trials, including binary hashes. Do not replace
final measurements with the fastest candidate sample.

## Native distribution

[Linux x86_64 and macOS arm64 native CI](https://github.com/adam2go/rowtrail/actions/runs/35682125721) passed at source commit
`1f8074446762270f8a432943bd4b646b2c32fd95`. Downloaded archives match their SHA-256/size manifests; extracted binary
hashes match the tested binaries. Apache-2.0, dependency notices and every size
budget were checked independently. The actual macOS package was installed and
passed all fifteen alpha.7 scenarios, the locally printed Python client, exact
large-integer composition and fresh-session catalog handoff.

[Published alpha.6 → native alpha.7](../../benchmarks/performance/alpha7/upgrade-native-macos.json)
verifies the one-way schema-5 → schema-6 upgrade, old fixed and partial results,
derived SQL, and the older runtime refusing the upgraded store.

| Platform | Compressed bytes | CLI bytes | Runtime bytes |
|---|---:|---:|---:|
| macOS arm64 | 19,139,452 | 3,716,960 | 100,133,712 |
| Linux x86_64 | 22,567,692 | 4,162,800 | 114,891,776 |

Budgets remain 30,000,000 / 4,500,000 / 125,000,000 bytes respectively. No external
package was added; Cargo.lock changes only the four workspace package versions.
Linux requires glibc 2.39+. Packages are unsigned engineering previews.
[Exact provenance and platform resource runs](alpha7-verification.json) ·
[Native check inventory](../../benchmarks/performance/alpha7/verification.json).

The release tag adds later documentation, charts and supplementary measurement
records. Executable sources, installer and CI checks match the verified source
commit. Bundled docs are the CI-time candidate snapshot; the repository contains
the completed report. Local timing results use the local build and retain its
separate hashes; no claim of identical CI-machine timings is made.

After publication, the [public installer probe](../../benchmarks/performance/alpha7/public-install.json)
downloaded the tagged installer and release asset without a version override.
Checksum-verified installation, exact large integers, workspace catalog and the
locally printed client's saved-result composition all passed. Installed binary
hashes match the independently verified macOS CI artifact.
