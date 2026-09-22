# Alpha.8: bounded parallelism and discoverable saved work

Status: implemented; native verification and repeated measurements are in
[the release report](../verification.md).

The alpha.7 primary-agent walkthrough needed three schema probes to recognize a
saved subset. Its four-partition experiment made a large workload faster, but
failed the same 32 MiB sort that worked serially. Alpha.8 addresses both costs
without adding an engine, model call, provider integration or external dependency.

## Descriptive labels and bounded metadata

`open` and `query` accept an optional label: nonblank, at most 256 UTF-8 bytes,
without control characters. It is an immutable description of that creation
request, not a unique name, SQL identifier or mutable alias. Duplicate labels are
allowed. The actual binding always names a frozen manifest or fixed revision.

`workspace summary` can filter by exact label. An indexed metadata table stores
labels in the same transaction as admission, including job/result references.
No separate commit is added. Unlabeled jobs add no label-index entries. The label
filter belongs to the cursor; changing it invalidates continuation. Page membership
is fixed while observed values can advance, as before. Counts describe the whole
workspace, not the filter. Tombstones retain descriptions and invalidity.

Dataset and result entries include a known row count (null when unknown), plus a
schema hint containing at most four complete fields and 512 encoded field bytes.
Long names/types are omitted, never silently abbreviated into different SQL
identifiers. `omitted_fields` makes this explicit. Full schema inspection remains
available. Metadata lookup does not rescan/checksum data or certify fresh validity.

Metadata becomes schema 7. The additive label index and persisted query options
upgrade atomically from 3/4/5/6; older runtimes refuse upgraded stores rather than
misinterpret durable job specifications. Keep a stopped, full pre-upgrade copy
for rollback. Old fixed results and progressive checkpoints remain readable.

## Resource-aware SQL planning

For SQL/prepare, automatic planning stays at one target partition when the sum of
captured input file lengths is below 16 MiB. Larger inputs target one partition
per 64 MiB of engine pool, capped at four and at available logical parallelism.
Thus the default 128 MiB pool permits two, while 32 MiB remains serial. File sizes
are planning hints, not estimates of decoded memory or projected scan bytes.

`execution.target_partitions` explicitly chooses 1..8. Values above one require
at least 64 MiB of pool per partition and fail validation before job admission if
that is not met. The selected target is reported in successful SQL job metrics.
The engine can still choose fewer partitions. This is neither a thread/RSS cap
nor a promise any arbitrary join/sort fits memory; budgets and cancellation still
terminate execution. There is no hidden retry with different settings after a
failure. Progressive analysis and export remain sequential and reject explicit
multi-partition targets.

The conservative pool gate avoids the known four-way 32 MiB merge-reservation
failure. It does not solve every engine resource case. Keep the broad sort/join,
scan-budget and cancellation matrix, and retain unsuccessful experiments.

## Evidence and launch boundaries

Large Arrow parts now use bytes already encoded by the IPC writer when deciding
whether to rotate. The next batch's full array-memory size remains conservative
headroom under the 6 MiB target. The 8 MiB encoded hard limit, 128-batch metadata
limit, 50 ms flush and immediate first available preview remain. Prepared Parquet
keeps its previous 4 MiB input-memory target; dictionaries remain separate.
Written batches are released, not accumulated in decoded memory. Checksums and
file/directory sync, SQLite FULL commit and acknowledgement ordering are unchanged.

This saves commits on compressible results, but whole-part verification makes
small reads of a large saved result more expensive. Retain first-page timings and
incompressible-data trials alongside large-query/sort gains; do not claim every
operation improves. Encoded part size is not a cap on decoded Arrow/RSS memory.

Direct comparison engines retain their intermediates. For automatic RowTrail
runs, controls receive its largest selected partition target across the workflow;
explicit comparisons can pin every engine to the same target. DuckDB threads and
DataFusion target partitions are disclosed, not equated to a hard CPU limit.

A runnable zero-download example opens generated orders, chooses a region from
bounded aggregates, saves a labeled branch without returning its rows, reconnects,
and checks the answer against Python integers. This is reproducible engineering
evidence, not a new paired-agent latency or token study. Alpha.6's fair pilot still
has no established overall RowTrail speed/token advantage over persistent DuckDB.

Public promotion should call this an Apache-2.0 engineering preview. Published
claims must link actual native verification and reproducible measurements; the
roadmap and unsupported platforms remain explicit.
