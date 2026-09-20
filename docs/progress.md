# Implementation progress

[Home](../README.md) · [Verification and measurements](verification.md)

Updated 2026-09-21 for **0.1.0-alpha.4**. This release adds restricted progressive Parquet file aggregation on top of
demand-driven inspection and explicit storage management.
It does not claim completion of all M2B–M6 roadmap work.

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
[Release provenance](release-verification.json).

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

- Further progressive work: row-group scheduling, sampling/estimates, reusable
  accumulator states and interrupted-run continuation are not implemented.
  `analyze` reports file-prefix coverage; ordinary SQL previews remain output
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
  inline binary cells and real-agent adoption experiments remain unimplemented.
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
python3 scripts/check_boundaries.py
python3 scripts/mcp_probe.py target/release/rowtrail
python3 scripts/session_probe.py target/release/rowtrail
python3 scripts/package.py
python3 scripts/install_probe.py
```

Use `scripts/cargo-local.sh` in place of Cargo when macOS needs the independent
Command Line Tools selection. The original private handoff remains outside Git.
