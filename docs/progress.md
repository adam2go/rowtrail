# Implementation progress

[Home](../README.md) · [Verification and measurements](verification.md)

Updated 2026-09-22 for **0.1.0-alpha.8**. Native builds, checksum installation, migration and repeated performance
measurements are verified; this remains an engineering preview.

## Alpha.8

- Resource-aware SQL parallelism keeps small or tight-memory jobs serial, uses
  bounded targets for larger inputs and reports the selected target. Explicit
  targets fail before admission when the engine-pool allowance is insufficient.
- Arrow publication uses actual encoded bytes and next-batch headroom, reducing
  commits for compressible results. The 8 MiB encoded / 128-batch bounds, immediate
  first preview, 50 ms flush, checksums and durability ordering remain unchanged.
- Optional descriptive labels on open/query, an indexed exact catalog filter,
  known row counts and bounded complete field hints simplify handoff. Duplicate
  labels retain separate immutable bindings; expired data stays expired.
- Schema 7 upgrades from 3/4/5/6. Old runtimes refuse upgraded job options; preserve
  a stopped full workspace copy for rollback. Existing fixed revisions remain.
- Eleven new integration scenarios cover reconnection, Unicode/budgets, labels,
  idempotency, aggregates/joins/sort across partition targets and cancellation.
- A Linux CI cancellation race is fixed: the durable stop reason governs both
  final state and error in one transaction. Deterministic child-exit tests cover
  unexpected exit, cancellation and timeout, without weakening exit confirmation.
- A zero-download example generates orders, saves a labeled branch, reconnects
  with one catalog call plus one query and verifies exact answers independently.
  It is bundled with the stdlib client and agent guides in native archives.
- Linux build baseline moves to Ubuntu 22.04 / glibc 2.35; the same archive also
  passes installation and the demo on Ubuntu 24.04. Installer rejects older
  glibc and musl before downloading. macOS arm64 remains supported.
- No external dependency is added. Native download sizes are 19.20 / 22.60 MB.
  Default million-row exploration is 236.41 → 144.23 ms (7 alternating trials);
  the 32 MiB sort is 929.18 → 462.06 ms (5 trials). One-partition exploration also
  improves. Larger saved-result pages regress by about 1.4 ms; entropy and small
  queries are essentially unchanged. All samples and tradeoffs are retained.
- A separate 50,000-result retained-history probe keeps late scalar queries near
  0.90 ms; fresh-coordinator catalog lookup takes 4.36 ms median with bounded
  output. Whole-workspace counts remain a scaling cost to optimize next.
- Native builds pass 99 integration scenarios and nine Rust tests each. The
  installed macOS archive passes the new scenarios, demo and published alpha.7
  upgrade again. Installer checks actual executables before switching links,
  including when an explicitly selected older package needs a newer libc.

[Design](decisions/008-bounded-parallelism-and-handoff.md) · [Measurements](verification.md).

Published [v0.1.0-alpha.8](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.8).
The public macOS installer selects alpha.8 by default; its actual downloaded
binaries, printed-client composition, labeled handoff and bundled demo pass.
[Public install](../benchmarks/performance/alpha8/public-install.json) ·
[Release provenance](release-verification.json).

## Alpha.7

- Arrow parts up to 128 KiB store their checked bytes and descriptor atomically
  in SQLite FULL; large parts keep file durability. A bounded staging buffer spills
  once. Inline bytes count against quotas and disappear with dependency-safe GC.
- One-way schema 6 upgrade preserves old fixed results and partial checkpoints;
  old runtimes refuse an upgraded workspace. Keep a pre-upgrade copy for rollback.
- Exact bounded Parquet ranges avoid reading gaps between projected columns.
  A job-local LRU retains up to 8 MiB / 128 verified parts, with hit/miss/peak
  counters; every new job revalidates. No cross-job result cache was added.
- Native responses include ready-to-use bindings. The optional locally printable
  stdlib client adds open/query/binding/rows, mechanical waiting without replay,
  and guards against treating partial/truncated observations as complete answers.
- Fifteen new integration scenarios; 88 total. Eight Rust tests include twelve
  publication-crash cases and a deterministic broken ACK pipe. Faster publication
  exposed a worker-loss classification race; the fix preserves committed revisions.
- Seven alternating local runs: 16K exploration 110.33 → 36.15 ms; 1M exploration
  296.09 → 239.95 ms. Warm scalar median 18.12 → 1.19 ms. Wide two-column projection
  reads 17.33 → 1.57 MB. Final reports retain all samples and binary hashes.
- Higher default parallelism was rejected after failing the 32 MiB sort. Adaptive
  startup polling had inconsistent benefit and was reverted. No new dependency.
- A primary-agent walkthrough uses printed helpers, saves without exposing rows,
  and reconnects with zero original reads. It reveals remaining catalog discovery
  work; no new fair paired-agent latency/token advantage is claimed.

[Design](decisions/007-agent-workflow-performance.md) · [Measurements](releases/alpha7-verification.md).
Native [macOS/Linux CI](https://github.com/adam2go/rowtrail/actions/runs/35682125721) and independent archive verification passed.
The installed macOS archive passes the new scenarios, printed-client composition,
handoff and upgrade from published alpha.6. Downloads are 19.14 / 22.57 MB.
[Release provenance](releases/alpha7-verification.json).

## Alpha.6

- Large Arrow result parts use Zstd level 1; small observations and progressive
  checkpoints stay plain IPC. Existing dependencies supply the codecs. A 64 KiB
  writer buffer reduces small writes; flush/file sync still precede publication.
- Arrow parts target 6 MiB with the existing 8 MiB encoded/128-batch hard bounds.
  Prepared Parquet keeps its 4 MiB target. First previews remain immediate;
  fewer later parts reduce durable commits without relaxing SQLite FULL sync.
- Page reads reuse column formatters and count metadata/cursor bytes directly.
  Typed rows, nulls, precision, projected names, quality and byte limits stay intact.
- The optional Python bridge has bounded mechanical waiting, single-schema
  discovery and a compact projection preserving complete observations and errors.
  [Minimal bootstrap](agent-quickstart.md) keeps the initial integration context small.
- Metadata remains schema 5. A two-version probe checks legacy plain parts,
  fixed partial revisions and alpha.5 reading/querying new compressed parts.
- Eight new integration scenarios, plus exact metadata-size arithmetic checks.
  Performance evidence includes rejected compression variants, high-entropy
  numeric reuse, complete exploration, paging and a real spill workload.
- The external-agent harness now profiles scalar and metadata-only DuckDB
  statements correctly; missing/incomplete profiles are never counted as zero.
  All 12 updated paired tasks pass, but RowTrail still uses more time/tokens than
  persistent DuckDB; shorter setup improves the exploration sample, not handoff.

[Design](decisions/006-result-performance.md) · [Measurements](releases/alpha6-verification.md).
Native [macOS/Linux CI](https://github.com/adam2go/rowtrail/actions/runs/35628707262) and independent artifact verification passed.
Installed packages pass the alpha.6 scenarios and bidirectional compatibility with
published alpha.5. Downloads are 19.16 / 22.42 MB (decimal).
[Provenance](releases/alpha6-verification.json).

## Alpha.5

- `analyze` defaults to complete Parquet row groups, including single-file inputs.
  One job-wide execution context, projected scans and a bounded footer cache avoid
  repeated planning. First/final checkpoints remain durable; intermediate prefixes
  coalesce at a configurable interval (50 ms default, 0 for every fragment).
- Coverage names actual fragments, files and processed rows. Arithmetic, immutable
  revisions, partial-result reuse, cancellation and resource budgets stay explicit.
  File mode plus interval 0 preserves the alpha.4 execution contract.
- `workspace summary` lists datasets, jobs and fixed result bindings within row/byte
  budgets. Pagination has fixed membership and live metadata; stored validity does
  not imply sources have just been revalidated. No data scan or model call.
- `guide`, `mcp-config`, method-specific MCP descriptions and typed recovery hints
  help a new agent connect and continue. No host configuration is edited implicitly.
- Metadata schema 5 upgrades schema 3/4 once, preserves old fixed revisions, and
  prevents old runtimes from reopening an upgraded store.
- Twelve new integration scenarios; 65 total. Million-row progressive completion
  is 49.18 ms locally versus 184.11 ms for an alpha.4 rerun (five repeats each).
  This includes coalescing 16 checkpoints down to two; it is not equal publication
  frequency. Final-only SQL is still faster (37.60 ms).
- A real external-agent paired pilot compares the same model with RowTrail and
  persistent DuckDB on exploration and saved-result handoff. All 12 trials answer
  correctly and both arms reuse the saved subset, but RowTrail is slower and uses
  more cumulative input tokens here. No broad efficiency/adoption claim; the product
  contains no model integration.

[Design and boundaries](decisions/005-row-groups-and-reconnection.md) ·
[Measurements and release status](releases/alpha5-verification.md). Native artifact verification
passed on [Linux and macOS](https://github.com/adam2go/rowtrail/actions/runs/35553205162); both archive hashes were checked independently.
The installed macOS archive passes row-group exploration, workspace handoff and
upgrade from published alpha.4. Downloads are 19.09 / 22.42 MB (decimal).
[Release provenance](releases/alpha5-verification.json). The complete agent pilot is published.

## Alpha.4

- `analyze`: cumulative count/sum/avg over complete Parquet manifest files, with
  1–16 aggregate descriptions and normal job/resource budgets.
- Replacement checkpoint revisions contain one row, stay immutable, and can be
  queried or exported explicitly. File coverage and finality remain separate.
- UInt64 counts; checked exact integer-unit sums; explicit Decimal average at
  scale 6 with truncation toward zero. Floats/grouping/filter expressions rejected.
- Budget/cancellation/worker-loss tests preserve the last full-file checkpoint;
  empty and overflowing inputs have explicit semantics.
- One-way schema-3 → schema-4 upgrade preserves old prefix revisions and prevents
  older runtimes from opening stores with new checkpoint semantics. A real old/new
  binary upgrade probe checks preserved values and derived SQL.
- Ten new integration scenarios (53 total). Ordinary session exploration measures
  104.03 / 319.67 ms for 16K / 1M rows locally. Progressive mode trades total time
  and checkpoint writes for earlier partial observations; it is not a general SQL
  speed optimization. [Design](decisions/004-progressive-file-aggregation.md).

The [macOS arm64 / Ubuntu 24.04 x86_64 CI matrix](https://github.com/adam2go/rowtrail/actions/runs/35526103291)
passed all checks. Downloaded archives pass independent hash verification; the
macOS archive passes installation, progressive-example execution and upgrade from
the published alpha.3 archive. Downloads are 19.10 / 22.36 MB (decimal).
[Alpha.4 release provenance](releases/alpha4-verification.json).

## Alpha.3

- One-query selected-column null counts and min/max, separate deterministic top-k,
  and fixed-revision result inspection. Scanning inspections are persistent jobs
  with the same execution budgets, cancellation and typed outputs as SQL.
- Explicit streaming CSV/TSV → managed Parquet preparation. No intermediate table
  collection, no implicit conversion, no usable dataset until the final commit.
  Empty, multi-file, typed Decimal, late-parse-failure and timeout cases are checked.
- SHA-256 verification on read, saved-result SQL, managed Parquet and export.
  Verification and decoding share bytes. One bounded part cache avoids rereading
  the same footer/body bytes. CPU acceleration uses runtime feature detection and
  a software fallback; supported platforms remain macOS and Linux.
- Workspace usage and conservative managed-data quota admission. Explicit pin,
  release, dry-run/apply GC; active and retained dependencies remain protected.
  GC persists expiration before deletion, and expired references fail explicitly.
- Test-only subprocess crashes at four commit boundaries for Arrow results and
  prepared Parquet. Recovery distinguishes visible commits from orphan files.
- All nine alpha.3 data contracts are available through MCP, CLI calls, NDJSON and the Rust
  client; dedicated CLI commands cover preparation and storage maintenance.
- A standard-library Python example runs open → profile → prepare → two saved
  branches → export → release/GC. The product still makes zero model calls.

The macOS arm64 and Ubuntu 24.04 x86_64 [native CI matrix](https://github.com/adam2go/rowtrail/actions/runs/35523791505) passed; both downloaded archive checksums were verified.

See [the design decision](decisions/003-demand-driven-storage.md),
[agent contracts](agent-guide.md), and [verification](verification.md) for evidence.

## Existing foundation

Fixed local manifests, read-only DataFusion SQL, typed parameters, immutable Arrow
results and fixed revisions/cursors, bounded observations, durable jobs/events,
idempotency, real cancellation, worker reuse, crash interruption, source refresh
and transitive invalidation. Two executables keep the client free of query-engine,
database and HTTP dependencies. Apache-2.0, dependency notices and distribution
size budgets remain mandatory. [Alpha.2 history](releases/alpha2-progress.md).

## Limits and remaining work

- Alpha.8 provides exact label filtering, row counts, bounded field hints and
  resource-aware query partition targets. Remaining handoff work includes query
  purpose, branch/SQL context, richer bounded discovery and representative paired
  agent tasks. Whole-workspace catalog counts still grow with retained history;
  broader above-memory parallel workloads need further verification.

- Ordinary SQL follows DataFusion type semantics: an Int64/UInt64 SUM can wrap
  when its accumulator overflows. Decimal(38,0) can represent a wider integer sum
  only while it fits the declared precision. Ordinary SQL can also produce a
  Decimal exceeding that precision, and its displayed value can be incorrect.
  Overflow can still be marked exact/complete and accepted by the current client:
  those checks do not certify arithmetic safety. Checked numeric aggregation and
  explicit numeric-quality semantics are priority correctness work. This is
  separate from JSON number preservation and from analyze aggregates, which
  explicitly reject overflow.

- Runtime socket discovery currently depends on the caller's TMPDIR. Changing
  TMPDIR while a coordinator owns the same workspace can cause a startup timeout
  instead of reconnecting. Keep the environment consistent when reusing that
  workspace; stable endpoint discovery and actionable connection errors remain
  priority reliability work. Transport failures must never silently replay jobs.

- Further progressive work: sampling/estimates, reusable accumulator states and
  interrupted-run continuation are not implemented. `analyze` reports fragment-prefix coverage; ordinary SQL previews remain output
  prefixes with unknown input coverage. Neither represents a population estimate.
- Top-k still performs an exact aggregation: k limits returned rows, not work.
  Broader above-memory join/window/high-cardinality workloads remain future work.
- Preparation is CSV/TSV storage conversion, not a prepared SQL plan cache.
  Retention is explicit; there is no automatic eviction or age-based policy.
- Quotas cover managed data plus result/spill reservations. SQLite/WAL, logs,
  event history, external sources/exports and process RSS are outside that limit.
  Reservations are deliberately conservative. Result and Parquet part verification
  may read a whole part even when the engine projects only a few columns.
- Source discovery: 128 external files / 4096 entries. Row profiles: 1–64 columns,
  one column for top-k, k ≤ 1000. Parts ≤ 8 MiB; public frames ≤ 1 MiB. Managed
  prepare manifests also have a metadata bound and fail explicitly when exceeded.
- Engine memory limits govern its pool, not all process buffers or RSS. Local
  mutable sources use identity/size/mtime checks rather than filesystem snapshots.
- External export file and sidecar publication is not one atomic filesystem
  transaction with SQLite; comprehensive export crash reconciliation is pending.
- Windows, remote sources, union-by-name, native MCP Tasks, automatic host resume,
  inline binary cells remain unimplemented. A small synthetic agent pilot is not
  evidence of adoption or broadly improved agent efficiency.
- Preview metadata is versioned with no migration promise. Packages are unsigned
  engineering previews. CSV sidecars are not automatically reimported as schema
  or quality; use Parquet when carrying those properties through reopening.

## Verify from source

```sh
cargo fmt --check
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo build --release --locked
cargo test --release --locked --workspace
python3 tests/integration/exploration.py --bin-dir target/release
python3 tests/integration/alpha3.py
python3 tests/integration/alpha4.py
python3 tests/integration/alpha5.py
python3 tests/integration/alpha6.py
python3 tests/integration/alpha7.py
python3 tests/integration/alpha8.py
python3 scripts/check_boundaries.py
python3 scripts/mcp_probe.py target/release/rowtrail
python3 scripts/session_probe.py target/release/rowtrail
python3 scripts/package.py
python3 scripts/install_probe.py
```

Use `scripts/cargo-local.sh` in place of Cargo when macOS needs the independent
Command Line Tools selection. The original private handoff remains outside Git.
