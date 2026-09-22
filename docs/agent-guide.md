# Agent integration

[Home](../README.md) · [Manual workflow](usage.md) · [Verification](verification.md)

For a small initial context, start with the [minimal bootstrap](agent-quickstart.md)
and discover individual schemas on demand.

RowTrail is for agents and their programs. It has no spreadsheet UI and makes no
model calls. Start with `rowtrail guide` for a machine-readable workflow. Discover a request contract with `rowtrail schema METHOD`; use
`rowtrail doctor` for actual capabilities. Open local CSV/TSV/Parquet with a
bounded schema observation, then bind the returned dataset and manifest IDs.

For tool hosts, launch `rowtrail --workspace /private/path mcp` over stdio. For
mechanical multi-step work inside the agent's code environment, launch
`rowtrail --workspace /private/path session` once. Both use the same API version
and durable workspace. The CLI is also available for one-off commands.

A session request is one JSON line:

```json
{"api_version":"1","request_id":"q1","idempotency_key":"analysis-step-1","method":"query","params":{"bindings":{},"sql":"SELECT CAST(9007199254740993 AS BIGINT) id","execution":{"wait_ms":200,"output":{"max_rows":10,"max_bytes":8192}}}}
```

`rowtrail --workspace /absolute/private/workspace mcp-config` prints an MCP server
configuration with the actual executable path. It does not edit host settings.
`guide`, `schema` and `mcp-config` do not start the runtime or create a workspace.

Read the corresponding response line before sending the next request. Request
and response frames are bounded to 1 MiB. Schema and help need no coordinator;
data sessions start one when needed. Relative paths are resolved by the client
process, so launch it in the intended working directory or use absolute paths.
Idle connections close after 60 seconds. Reopen a session when needed; workspace
objects and accepted jobs persist independently of the connection.

- Check `ok`, then `job.state`. Acceptance is not computation success.
- Consume an included `observation` directly. Do not make an extra `read` call
  for information already returned.
- If still running, retain `job.id`. Use `control` with `action=wait`, `ref` and
  `wait_ms`, or inspect later. `wait_ms=0` accepts work without waiting.
- Retain `job.result_ref` and `readable_revision`. Bind this fixed result for
  follow-up SQL; do not print the full table and import it again. A new dimension
  absent from a saved projection requires going back to the original binding.
- Follow read cursors unchanged. Keep Int64/UInt64/Decimal JSON strings intact;
  their schema types describe arithmetic meaning.
- Read accuracy, coverage and `final_for_request` separately. A query over a
  partial result can finish without covering the original source completely.
- Use explicit `control cancel` to stop unnecessary work. Closing stdin or
  disconnecting does not cancel an accepted job.
- A session is sequential. A second connection can cancel or inspect a job
  while the first connection waits. After a transport error or interrupted RPC,
  open a new session. Reuse the original idempotency key when choosing to retry;
  the client never automatically replays a potentially accepted mutation.

The Python [bridge](../examples/session_client.py) and [workflow](../examples/explore.py)
need only the standard library and are optional. The installed CLI prints the
same bridge with `rowtrail python-client > rowtrail_client.py`; no network or
package installation is needed. `open(source)` and `query(sql, bindings)` retain
full responses and accept previous responses or catalog items as fixed bindings.
`query` submits once, waits mechanically, and fetches one bounded page only when
needed. It raises `JobNotCompleted` with the full `.response` on failure or a
helper deadline. `rows(response)` requires a valid, exact, final, complete,
untruncated included observation; it does not automatically paginate. For partial
or asynchronous work use `call`, `finish`, `observe`, and explicit bounded reads. The bridge also has `schema(method)`,
`finish(response, timeout=30)` and `observe(response)`: discover locally, wait
mechanically, and print a compact job view while keeping the full response in
code. Always check the returned state; a helper deadline does not cancel a job.
The complete typed observation, quality, fixed binding and error are preserved. Rust applications can use
[`Client::session()`](../crates/client/examples/query.rs) without linking the
query engine. Neither entry requires a model API key.

For ordinary SQL, cast values to `DECIMAL(38,0)` before summing when the sum
can exceed a 64-bit integer. SQL follows engine type semantics; the checked
progressive aggregate contract below is separate.

Sampling, a SQL prepared-plan cache, native MCP Tasks and automatic model resume
are not implemented. These must not be simulated by the integration host and
presented as capabilities of RowTrail.

## Reconnect without guessing references

Call `workspace` with `{"action":"summary","limit":20,"max_bytes":8192}`.
It lists datasets, jobs and results with fixed bindings, stored validity, coverage
and suggested next actions. Reuse an item's `binding` for SQL, or retain a job ID
and explicitly wait/cancel. Filter with `kind: "dataset"`, `"job"` or `"result"`.
Optional `open.label` and `query.label` describe a created object. Filter summary
by exact `label` (up to 256 UTF-8 bytes, nonblank, no control characters). Labels
are not unique; select the intended fixed binding. Dataset/result items include
`row_count` (null if unknown) and `schema_hint`: at most four complete fields and
512 encoded field bytes. `omitted_fields` indicates when full inspection is needed.
Names are never silently shortened. Filtering uses an index, not a scan of every
stored SQL specification.

Counts describe stored state: known-invalid results are excluded from the readable
count, while unobserved source changes remain unknown. Follow `next_cursor` unchanged with the same kind and label; it fixes page membership against
new insertions, while each page reads current metadata. Counts cover the workspace.
This is a bounded metadata catalog: it does not scan sources or generate prose.
Stored validity is checked again when a binding is used. Expired tombstones are
explicit; finding a reference does not make expired data usable.

The summary reconnects an agent to durable objects; it does not resume an
interrupted calculation. Retry a mutation only with its original idempotency key.

## Inspect only what the next question needs (alpha.3)

```json
{"ref":"mf_ID","columns":["amount","units"],"checks":["null_count","min_max"],"execution":{"wait_ms":200,"scan_bytes":1073741824},"budget":{"max_rows":10,"max_bytes":8192}}
```

Send this to `inspect` / `data_inspect`. It returns a query job and a reusable
result. `quality.inspection.outputs` describes each typed statistic. For common
values, use one column and `checks:["top_k"], top_k:10`. Top-k is exact over the
requested input, including null; a small k does not make aggregation free. Keep
checks and selected columns narrow. Profiles have the same quality distinctions
and execution budgets as SQL. `budget` controls the response; `execution` controls
work. On results, include `ref:res_ID` and a fixed `revision`.

## Prepare when repeated parsing is worth avoiding

```json
{"source":{"dataset_ref":"ds_ID","manifest_ref":"mf_ID"},"execution":{"wait_ms":200,"result_bytes":536870912,"spill_bytes":67108864}}
```

Send to `prepare` / `data_prepare`. Keep the returned job and proposed dataset /
manifest references. Bind them **only after `job.state == completed`**; a completed
wait/status response also includes `job.prepared`. Failure does not publish a
partial dataset. Conversion preserves the frozen CSV schema, writes managed
Parquet, and keeps an independent immutable copy. It costs time and space up front;
use the [measured preparation workload](../benchmarks/performance/alpha3/prepare-1048576.json)
to understand the tradeoff, not as a universal threshold.

## Release storage explicitly

- `workspace {"action":"usage"}` reports stored bytes and conservative reservations.
- `workspace {"action":"configure","quota_bytes":2147483648}` enables a 2 GiB
  managed-data quota; 0 disables it. Set realistic per-job result/spill budgets.
- `control {"action":"pin","ref":"res_ID"}` retains a result or prepared dataset.
- `control {"action":"release","ref":"res_ID"}` makes it eligible for collection.
- `workspace {"action":"gc"}` previews collection; add `"dry_run":false` to apply.

Retained children and active jobs protect their inputs. Release a complete unused
branch before GC. Expired objects fail explicitly; do not reinterpret that failure
as an empty table. Metadata, logs, events and external exports are outside this
quota and GC. The [runnable workflow](../examples/prepare_explore.py) profiles,
prepares, branches twice, exports and collects exactly its own intermediate data.

## Progressive row-group aggregation (alpha.5)

Send this to `analyze` / `data_analyze` / `rowtrail analyze --request request.json`:

```json
{"source":{"dataset_ref":"ds_ID","manifest_ref":"mf_ID"},"aggregates":[{"function":"count","alias":"rows"},{"function":"count","column":"amount","alias":"nonnull"},{"function":"sum","column":"amount","alias":"total"},{"function":"avg","column":"amount","alias":"mean"}],"execution":{"preview":"available","wait_ms":200}}
```

The source must be Parquet. Previews are cumulative snapshots over complete row
groups in frozen file/row-group order. Each revision contains **one aggregate row**; later
checkpoints replace the snapshot, not earlier immutable revisions. Read
`quality.coverage.input_coverage.completed_fragments`, `total_fragments`,
`processed_rows`, `completed_files` and `total_files`. `total_fragments` can be null
for older/prepared manifests until all footers are read; completed final coverage
has an exact total. These are prefix coverage, not statistical estimates.
Keep `result_ref` and the exact revision before branching.
A finished query on a partial checkpoint still has partial original-source coverage.

Sum/avg accept Int64, UInt64 and Decimal128 scale 0–6. Counts are UInt64; sums use
Decimal128(38,input_scale). Average is Decimal128(38,6), truncating toward zero.
Request sum plus non-null count if the caller needs a rational mean. No floats,
GROUP BY, filters, SQL expressions, sampling, estimates or interrupted-run resume
are included. A single large file can now supply many fragments. The first complete
fragment is published immediately; later checkpoints coalesce with
`checkpoint_interval_ms:50` (default). Set 0 to publish every fragment, or up to
60000 to reduce writes. This interval is checked at fragment boundaries, not a
wall-clock response deadline. The final prefix is always published.
`preview:none` writes one final result. For alpha.4 file-prefix behavior, explicitly
set `fragment_unit:"manifest_file", checkpoint_interval_ms:0`.
Details: [row-group design](decisions/005-row-groups-and-reconnection.md) and
[arithmetic contract](decisions/004-progressive-file-aggregation.md).

Alpha.8 upgrades metadata once from schema 3/4/5/6 to 7. Existing fixed revisions
remain readable. Alpha.7 and earlier runtimes refuse an upgraded store: retain a
workspace backup if you need to keep using an older runtime.

Arrow IPC parts up to 128 KiB of encoded data now live in SQLite, committed with
their descriptors; larger parts retain the durable file path and optional Zstd
compression. Both count against result and managed-data quotas. GC deletes inline
BLOBs but SQLite may reuse freed pages without shrinking its physical file.
Database/WAL overhead remains outside the logical quota. Verification reads whole
parts, so projection does not eliminate integrity I/O. The existing 8 MiB
verified cache now holds multiple parts (up to 128) within one job. New jobs
reverify bytes; there is no cross-job cache that bypasses corruption detection.

## SQL parallelism and part sizing

Automatic SQL planning keeps input sets below 16 MiB serial. Larger inputs target
one partition per 64 MiB of engine pool, capped at four and available logical
parallelism. The 128 MiB default allows two; a 32 MiB pool stays serial. Explicit
`execution.target_partitions` accepts 1..8, with at least 64 MiB of pool per
partition when greater than one. It fails validation before admission otherwise.
`job.metrics.target_partitions` reports the planning target, not a thread/RSS
limit or a guarantee every query fits. Pin one for a serial baseline. Progressive
analysis and export remain sequential and reject explicit multi-partition targets.

Large Arrow parts use encoded bytes plus conservative next-batch headroom to
coalesce compressible output, within the same 8 MiB encoded/128-batch limits.
First available previews and 50 ms flush remain. Fewer durable commits make large
materialization cheaper; reading a small page from a larger part still verifies
that whole part. See the measured tradeoffs in [verification](verification.md).
