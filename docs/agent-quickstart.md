# Minimal agent bootstrap

Use a persistent `rowtrail --workspace PATH session` (NDJSON), or stdio MCP.
Discover only the request contract you need with `rowtrail schema METHOD`.
No model calls occur inside RowTrail. The optional standard-library Python bridge
in `examples/session_client.py` exposes `rt.call(method, params)` and local
`rt.schema(method)`; keep variables and responses in your code environment.

1. `rt.call('open', {'source': source})` returns `dataset_ref`, `manifest_ref` and
   typed fields. Bind the two refs as `{'t': binding}` in `query`.
2. Query: `{'bindings': {'t': binding}, 'sql': 'SELECT ... FROM t',
   'execution': {'wait_ms': 1000, 'output': {'max_rows': 10, 'max_bytes': 8192}}}`.
   `rt.finish(response)` mechanically waits up to 30 seconds. Always check
   `response['job']['state']`; a deadline or accepted job is not success.
3. Print `rt.observe(response)` for a compact job view. Consume an included
   observation directly. Otherwise `read` with `result_ref` and the exact
   `readable_revision`. Reuse that fixed binding in SQL; do not print/import
   a large intermediate table. Keep full responses in code for diagnostics.
4. On handoff, `workspace` with `{'action':'summary','kind':'result',
   'limit':10,'max_bytes':8192}` discovers saved bindings and stored validity.
   Follow `next_cursor` unchanged. Catalog validity is not a fresh source scan.

Rows are positional with a schema. Int64/UInt64/Decimal values remain strings;
use exact arithmetic. For SQL sums that may exceed 64-bit range, cast the
input to DECIMAL(38,0) before summing. Preserve observation quality, coverage, final_for_request,
validity and presentation/next_cursor. A completed query on a partial input still
has partial source coverage. Partial analyze checkpoints replace earlier rows.

Compose mechanical operations in one code call and print only needed observations.
After ambiguous transport failure, reconnect and inspect durable state; never
silently replay a mutation. `control cancel` explicitly stops work; disconnecting
or a helper deadline does not. See `docs/agent-guide.md` when more detail is needed.
