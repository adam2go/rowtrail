The persistent environment provides rt.call(method, params), returning the result object or raising an error. It uses RowTrail. rt.reconnect() creates a fresh transport without replaying any calls. Use workspace summary to find saved bindings. API request schemas are in schemas.json.
# Agent integration

[Home](../README.md) · [Manual workflow](usage.md) · [Verification](verification.md)

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

## Reconnect without guessing references (alpha.5)

Call `workspace` with `{"action":"summary","limit":20,"max_bytes":8192}`.
It lists datasets, jobs and results with fixed bindings, stored validity, coverage
and suggested next actions. Reuse an item's `binding` for SQL, or retain a job ID
and explicitly wait/cancel. Filter with `kind: "dataset"`, `"job"` or `"result"`.
Follow `next_cursor` unchanged with the same kind; it fixes page membership against
new insertions, while each page reads current metadata. Counts cover the workspace.
This is a bounded metadata catalog: it does not scan sources or generate prose.
Stored validity is checked again when a binding is used. Expired tombstones are
explicit; finding a reference does not make expired data usable.

The summary reconnects an agent to durable objects; it does not resume an
interrupted calculation. Retry a mutation only with its original idempotency key.

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
need only the standard library and are optional. Rust applications can use
[`Client::session()`](../crates/client/examples/query.rs) without linking the
query engine. Neither entry requires a model API key.

Sampling, a SQL prepared-plan cache, native MCP Tasks and automatic model resume
are not implemented. These must not be simulated by the integration host and
presented as capabilities of RowTrail.

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

Metadata upgrades once from schema 3/4 to 5. Existing fixed revisions remain
readable, but older runtimes refuse an upgraded workspace. Back up a workspace
before upgrading if you need to keep using the old runtime.
