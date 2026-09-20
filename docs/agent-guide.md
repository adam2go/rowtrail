# Agent integration

RowTrail is for agents and their programs. It has no spreadsheet UI and makes no
model calls. Discover a request contract with `rowtrail schema METHOD`; use
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

Sampling, prepared execution, native MCP Tasks, automatic model resume and GC
are not implemented. These must not be simulated by the integration host and
presented as capabilities of RowTrail.
