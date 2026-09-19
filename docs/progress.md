# Implementation progress

Updated 2026-09-20 for 0.1.0-alpha.1. This is the first M0 → M1 → M2A engineering
delivery. It is not the complete M2B–M6 local product.

## Implemented and verified locally

- Locked Rust 1.94.0, DataFusion 55.0.0 family, Arrow/Parquet 59.2.0, real spill
  and cancellation probes. CLI/client dependency boundaries are checked.
- Private local IPC, automatic coordinator startup, exclusive worker reuse,
  persistent jobs/revisions/events, normalized idempotency and truthful failures.
- Frozen local CSV/TSV/Parquet manifests, bounded CSV inference and Parquet footer
  discovery, explicit schemas, bounded schema/head observations and field paging.
- Read-only SQL with explicit dataset or fixed-result bindings and typed
  parameters; streamed immutable Arrow parts, stable result pagination, exact
  integer/Decimal JSON representation, CSV/Arrow/Parquet export with sidecars.
- Real background jobs; wait and execution deadlines are independent. Cancellation
  and failure replace the worker after observed exit. Crashes interrupt attempts
  and preserve committed results. Successful jobs release their execution context
  while retaining an idle worker for the next job.
- Source/result read counters, result-write accounting, engine pool/spill budgets,
  physical scan reservations (including export), bounded serialized observations
  and errors. Shared-runtime clients resolve relative paths in their own working
  directories. Error envelopes reserve a minimum 512-byte budget; truncated error
  text and identifiers are explicitly flagged.
- Best-effort source version checks and transitive invalidation. Refresh makes a
  new manifest. Partial input quality propagates through derived queries and
  Parquet export/reopen, including multi-file imports.
- Real MCP stdio handshake and generated tool schemas; an MCP-created result is
  read through CLI. The lightweight Rust SDK example checks the same large integer.
- Durable event replay and an atomic job snapshot with a compatible event cursor.
- Apache-2.0 project license, 298 dependency declarations/notices, reproducible
  packaging scripts and a macOS/Linux GitHub Actions workflow.

## Evidence

The local release binaries pass 27 deterministic end-to-end checks. These cover
hand-calculated CSV/four-row-group Parquet S1, branching saved results in S2, a
16,384-row integer reference, worker reuse, precision/pagination, query and export
budgets, timeouts, true cancellation, idempotency, read-only SQL, corrupt result
files, coordinator/worker crashes, empty results/CSV headers, partial results,
event replay, source invalidation and refresh. No tests call a model.

`cargo fmt --check`, Clippy with warnings denied, workspace tests (including two
error-type checks), dependency boundaries, MCP cross-entry consumption, and the
SDK precision example pass on **Ubuntu 24.04 x86_64 and macOS 14 arm64** in
[the final CI run](https://github.com/adam2go/rowtrail/actions/runs/35459097860). Each platform passes all 27 end-to-end checks.
Both package checksums were verified after download; the macOS CI archive was
also extracted and executed locally. Artifact hashes and provenance are in
[release-verification.json](release-verification.json).

The five-repeat initial comparison is recorded in `benchmarks/baseline.json`.
RowTrail's median is about 379 ms versus 14 ms for persistent DuckDB and 5 ms for
persistent direct DataFusion on this small workload. No stable performance or
Agent-adoption advantage is claimed. See `benchmarks/README.md` for boundaries.

## Remaining work and explicit limitations

- M2B: broader sources and complex SQL/fault coverage, deep/wide metadata paging,
  fuller physical resource accounting, stronger result integrity checks on every
  derived-input path and a wider above-memory workload matrix.
- M3: full inspect/profile controls, prepared-view execution and SDK ergonomics.
  Current fixed views/scopes expose definitions; they are not a prepared-plan cache.
- M4: constrained progressive Parquet aggregation, sampling/estimates, reusable
  shard aggregate states, and estimate-to-exact continuation are not implemented.
- M5: reference host resume, native MCP Tasks negotiation and external Agent trials.
  Events are durable and replayable, but do not themselves resume a model run.
- M6: workspace quotas, pin/release/GC, systematic crash-at-commit injection, full
  cost accounting and release hardening. Results/events currently remain until
  the workspace is removed while no runtime is using it.

Source discovery currently supports at most 128 files / 4096 directory entries;
larger inputs fail explicitly. Result parts are bounded to 8 MiB and public frames
to 1 MiB. Engine memory accounting is not an RSS hard limit. Mutable local files
use identity/size/mtime checks, not filesystem snapshots. CSV exports preserve
quality in sidecars; reopening CSV does not automatically apply that sidecar or
its schema. Use Parquet to carry quality metadata through RowTrail reopening.
Windows, remote sources, union-by-name, native Tasks, automatic host resume and
inline binary values are not advertised. Local metadata is versioned; incompatible
preview stores fail explicitly and have no migration promise. Binary packages
are unsigned engineering previews.

## Continue development

```sh
cargo build --release --locked
python3 tests/integration/exploration.py --bin-dir target/release
python3 scripts/mcp_probe.py target/release/rowtrail
python3 scripts/package.py
```

Preserve the fixtures and quality contracts while extending capabilities.
Prioritize resource/integrity coverage and programmatic combination costs before
claiming an adoption benefit. The original private handoff is outside this Git
repository; public documentation here is maintained separately.
