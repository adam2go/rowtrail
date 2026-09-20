# Alpha.3: demand-driven inspection and managed data

Status: implemented; release verification is recorded in `docs/verification.md`.

## Inspection

`open` remains bounded discovery. `inspect` compiles `null_count` and `min_max`
for 1–64 selected columns into **one** aggregate query. Results retain their Arrow
value types; `quality.inspection.outputs` maps output fields to original columns.
`top_k` is a separate single-column GROUP BY with deterministic count/value ordering,
including null as a category. Its limit bounds returned groups, not computation:
high-cardinality aggregation still requires engine memory/spill and scan budgets.
`head` remains a separate limited query. Row inspection on a result requires an
explicit revision. Every scanning inspection is an ordinary durable, cancellable
job; schema-only inspection remains metadata-only.

## Preparation

`prepare` explicitly converts one frozen CSV/TSV manifest to managed Parquet.
It uses the existing worker, memory/spill/scan/time/result budgets and backpressure.
Snappy compression is already in the dependency graph. Parts target 4 MiB of
uncompressed batch data, have an 8 MiB encoded limit, and use bounded row groups.
There is no full-table collection or intermediate Arrow materialization.

A single terminal SQLite transaction publishes the new dataset/manifest. Sealed
parts before that transaction are internal and never represent a usable partial
dataset. Empty inputs still produce a typed empty Parquet file. Input type failures,
source changes, cancellation or budget exhaustion cannot publish a completed copy.
A completed copy is independent of subsequent mutations to the original source;
its quality and lineage remain explicit. This is a storage conversion, not a SQL
prepared-plan cache. It is never triggered implicitly by `open` or a query.

## Integrity

Displayed, exported and query-bound result parts use SHA-256 and parse the **same
bytes that were verified**. Managed Parquet parts use the same path. One verified
part (at most 8 MiB) is cached per job object store so footer/body range requests
can reuse its bytes. Physical verification reads consume the scan budget and are
reported as result bytes. The cache is outside the engine memory pool; Arrow,
Parquet and execution buffers also make the pool limit different from an RSS cap.
Prepared-column pruning may therefore save decoding but does not eliminate the
full-part integrity read. Benchmarks include that cost.

## Storage lifecycle

Results and prepared datasets are retained by default. `control pin` explicitly
retains a managed object; `release` removes that pin and marks it collectible.
`workspace gc` defaults to a dry run. Applying it protects retained/pinned ancestors
and every queued/running/stopping job dependency. Read operations and GC coordinate
so an in-flight page cannot lose its files. Durable tombstones precede file unlink;
repeating GC after a crash is safe, and expired references return `OBJECT_EXPIRED`.
GC processes up to 1,000 objects per call and cleans abandoned staging/spill dirs.
It does not delete user source files, exported files, metadata or event history.

`workspace configure` sets a byte quota (0 disables it). Admission conservatively
adds stored parts, active result/spill reservations and the new request's budgets.
Reserved active output may also be counted as stored after publication; this can
reject early but cannot allow extra output through. The quota covers managed data
and reservations, **not** SQLite/WAL, logs, external sources/exports or process RSS.
Without a configured quota, query admission skips the workspace size scan.

## Crash boundaries

Unit-test-only subprocess hooks terminate immediately after file rename, after
part commit, before terminal commit and after terminal commit. Both Arrow result
and prepared-Parquet paths are exercised. Recovery preserves committed previews,
keeps unpublished preparation invisible, and removes orphaned unindexed parts.
These hooks are absent from production binaries. Disk/SQLite and multi-file
external export are not one universal atomic filesystem transaction; export
crash recovery remains a separately documented limitation.
