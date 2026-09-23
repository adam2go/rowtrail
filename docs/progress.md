# Implementation progress

[Home](../README.md) · [Verification and measurements](verification.md)

Updated 2026-09-24 for **0.1.0-beta.3**. Native macOS/Linux verification and
independent artifact checks pass. [Release evidence](release-verification.json).

## Beta.3

- Optional compact native responses preserve typed answers, full quality/numeric
  policy, job failures, fixed bindings and truncation. Operational details remain
  at control/status; no persisted job semantics or dependency is added.
- Bounded wait observations remove the separate post-wait read. Included fixed
  revision row counts remove a redundant metadata call from SQL assertions.
- Metadata schema-name search and contextual inspection recover relevant fields,
  purpose, SQL and input bindings. Long definitions are explicitly omitted whole.
- Python query(fetch=False) saves intermediates with zero observed rows. The
  bundled `rowtrail demo` generates its own wide data, retains full transcripts,
  verifies answers and hands a portable branch to a separate recipient process.
- Same schema 9 as beta.2. No model calls; no new engine or CLI dependency. CLI
  alone uses one codegen unit and opt-level=2; engine optimization stays unchanged.
- Fourteen new integration scenarios cover budgets, search, context, numeric
  evidence, errors, partial coverage, waiting, checks and the offline handoff.
  Both native platforms pass all 143 integration scenarios and 14 Rust tests,
  public-release upgrades, installation and demo checks. The same Linux archive
  also installs on Ubuntu 24.04.
- Seven final local trials: compact response tokens fall 27.7%; the sum of request
  and response medians falls 22.2%. This is JSON tokenization, not model billing.
  Direct DuckDB remains faster; all earlier series and small regressions are kept.
- Native downloads are 19.43 MB macOS / 22.88 MB Linux, under unchanged ceilings.
  CLI opt-level=3 exceeded Linux's size budget; only the CLI moved to opt-level=2.
  Engine optimization, fsync, checked numerics and full quality remain unchanged.

[Native CI](https://github.com/adam2go/rowtrail/actions/runs/35898544833) ·
[Published beta.3](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-beta.3).
The public default installer, exact native hashes, installed demo and handoff
also pass [post-publication acceptance](../benchmarks/performance/beta3/public-install.json).

[Agent demo](agent-demo.md) · [Decision](decisions/011-agent-context-and-demo.md).

## Beta.2

- Native independent snapshots accept Parquet/CSV/TSV datasets and completed exact
  result revisions. Existing prepare publication, checksum, quota, cancellation
  and fsync guarantees are reused. Labels and bounded provenance survive copies.
- Provenance inspection distinguishes stored validity, actual part checks and
  source independence. Existing source invalidation semantics remain explicit.
- Small responses prioritize answers over optional metrics. A 2 KiB scalar can
  be consumed in one call with complete quality and fixed references intact.
- The stdlib client adds lossless scalar/named records, exact SQL assertions and
  schema leakage checks, keyed diffs, versioned recipes with durable run records,
  and portable branches with Markdown, SQL, identity, quality and Parquet payloads.
- Packages are checksum-verified directories; import never runs SQL or reads
  recorded original paths. Fresh copies support follow-ups without originals;
  full recomputation is an explicit recipe run with complete input mappings.
- Per-step idempotency keys are written before recipe submission. Read-only
  `control/lookup` recovers committed acceptance without replay. Earlier successes
  and unknown transport outcomes remain inspectable.
- Higher-level analysis helpers are optional Python composition, not native MCP
  tools. Diff scans more than once; packages/recipes are not whole-operation
  transactions. Metadata/payload/step budgets fail explicitly. No scheduler,
  dataframe library or external dependency is added.
- Metadata schema 9 upgrades 3..8 and excludes old runtimes. Public alpha.8 and
  beta.1 upgrades, exact old revisions, crash recovery and native size ceilings
  remain release gates.

- Both native platforms pass 129 integration scenarios and 14 Rust tests. The
  downloaded macOS package passes the new suite, real beta.1 upgrade and both
  installed demos again. [Native CI](https://github.com/adam2go/rowtrail/actions/runs/35823132374).
- Downloads are 19.42 / 22.82 MB; all 557 notices and hashes match. The 2 KiB
  scalar saves one call and 31% of response bytes. Existing large workflows
  remain broadly stable, with all samples and regressions disclosed. The full
  131,072-row handoff workflow takes 864 ms median across five local trials.

[Published beta.2](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-beta.2) ·
[Artifact provenance](releases/beta2-verification.json) ·
[Verified public default installation](../benchmarks/performance/beta2/public-install.json).

[Analysis guide](analysis.md) · [Runnable complete example](../examples/analysis_quickstart.py) ·
[Decision](decisions/010-portable-analysis.md) · [Verification](verification.md).

## Beta.1

- Checked integer/Decimal SQL SUM across scalar, grouped, DISTINCT, sliding-window
  and real partial-merge paths. Wide checked state avoids batch-dependent narrow
  overflow. Decimal precision is guarded before persistence, read and export.
- Numeric policy v1 separates sampling exactness from finite SQL arithmetic.
  Structured overflow errors retain operation/type/expression and recovery.
  Old revisions remain immutable; unknown numeric provenance is never recertified.
- Stable short workspace sockets ignore TMPDIR. A private endpoint descriptor and
  live handshake check UID, workspace, store, PID and runtime version. Typed
  connection failures expose recovery paths without replaying accepted work.
- The optional stdlib client composes prepare/inspect/export, strict unique label
  lookup, explicitly budgeted paging and lossless int/Decimal conversion. Exceptions
  retain full durable state and distinguish pagination exhaustion from EOF.
- Saved IPC files stay whole during scanning; separate files remain parallel.
  This removes format sniffing and repeated full-part verification from range
  partitioning while retaining the job-local 8 MiB cache and all durability rules.
- Schema 8 upgrades 3/4/5/6/7 and excludes old runtimes. Local tests preserve old
  fixed and partial revisions and reject invalid old Decimal display/export.
- 113 integration scenarios and 14 Rust tests pass locally and on both native
  build platforms, including the original subprocess publication/cancellation tests. Native CI gates also fetch the
  checksum-pinned published alpha.8 package and test its real upgrade.
- Seven alternating local trials: save large subset, ten follow-ups and reconnect
  takes 934.02 → 719.63 ms; ten-query time 567.27 → 375.18 ms. Each follow-up reads
  82.03 → 28.16 MB of saved data, zero original bytes. Save time +4.3%, separate
  million-row exploration +4.0%, response bytes +9.2%; warm p95 also regresses.
- Native downloads are 19.37 / 22.71 MB, with no added external dependency. SHA-256,
  executable test hashes and all 557 notices match. The same Linux archive also
  passes Ubuntu 24.04 installation and its bundled demo. The actual macOS archive
  passes the new suite, real old-version upgrade and installed demo again locally.

[Release](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-beta.1) ·
[Native CI](https://github.com/adam2go/rowtrail/actions/runs/35757844420) ·
[Artifact provenance](releases/beta2-verification.json) ·
[Public default install](../benchmarks/performance/beta1/public-install.json).
The public beta.1 installer downloads the verified macOS pair; printed-client
composition, cross-TMPDIR labeled handoff, overflow rejection and the demo pass.

[Decision](decisions/009-numeric-reconnection-and-reuse.md) ·
[Numeric contract](numeric-contract.md) · [Verification](verification.md).

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

[Design](decisions/008-bounded-parallelism-and-handoff.md) · [Measurements](releases/alpha8-verification.md).

Published [v0.1.0-alpha.8](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.8).
At publication, the public macOS installer selected alpha.8 by default; its downloaded
binaries, printed-client composition, labeled handoff and bundled demo pass.
[Public install](../benchmarks/performance/alpha8/public-install.json) ·
[Release provenance](releases/alpha8-verification.json).

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
  catalog context, richer bounded discovery and representative paired agent
  tasks. Beta.2 stores caller purpose/provenance and exports SQL branch context;
  full SQL remains outside the default compact catalog. Whole-workspace catalog counts still grow with retained history;
  broader above-memory parallel workloads need further verification.

- Integer/Decimal SQL SUM and Decimal publication boundaries are checked in beta.1.
  Other SQL arithmetic (including AVG, scalar expressions, casts and floating-point
  SUM) retains engine semantics. An old wrapped integer cannot be identified from
  its saved value alone. Missing legacy numeric policy means unknown; see the
  [numeric contract](numeric-contract.md).
- New coordinators reconnect independently of TMPDIR. An already-running old
  coordinator must finish and exit before a new version can own its workspace;
  clients report version/endpoint errors instead of killing active jobs.

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
- Pre-1.0 metadata may still change; documented one-way migrations are tested.
  Packages are unsigned beta previews. CSV sidecars are not automatically reimported as schema
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
python3 tests/integration/alpha9.py
python3 tests/integration/beta2.py
python3 tests/integration/beta3.py
python3 scripts/previous_release_probe.py
python3 scripts/check_boundaries.py
python3 scripts/mcp_probe.py target/release/rowtrail
python3 scripts/session_probe.py target/release/rowtrail
python3 scripts/package.py
python3 scripts/install_probe.py
```

Use `scripts/cargo-local.sh` in place of Cargo when macOS needs the independent
Command Line Tools selection. The original private handoff remains outside Git.
