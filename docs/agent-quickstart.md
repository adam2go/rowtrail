# Minimal agent bootstrap

Use one persistent session and keep responses in your code environment. The
optional Python client needs only the standard library; obtain it locally with
`rowtrail python-client > rowtrail_client.py`, then import `RowTrail` and create
`rt = RowTrail(binary='rowtrail', workspace='/absolute/private/workspace')`.
The product itself requires neither Python nor a model API key.

```python
t = rt.open(source)                       # bounded fields + frozen binding
print(t['fields'])                        # discover the actual column names
r = rt.query('SELECT COUNT(*) AS n FROM t', {'t': t})
print(rt.observe(r))                      # full typed observation and quality
saved = rt.query('SELECT * FROM t WHERE amount IS NOT NULL', {'t': t},
                 execution={'output': {'max_rows': 0, 'max_bytes': 8192}})
next_result = rt.query('SELECT SUM(amount) FROM saved', {'saved': saved})
print(rt.observe(next_result))
```

Use SQL appropriate to the discovered fields. `query(sql, bindings={},
parameters=[], execution={}, timeout=30, idempotency_key=None)` accepts an open,
query, read, or catalog item as a binding. It submits once, waits mechanically,
and fetches one bounded page only if the reply has none. Defaults: final-only
SQL, 1-second initial wait, then up to 30 seconds of waiting; 100 rows / 8 KiB
output. It preserves the full response. `JobNotCompleted.response` retains the
job and any partial result; a deadline never cancels or replays work.

`rt.rows(r)` returns the included wire rows only when the job is completed and
the observation is valid, exact, final, complete in source coverage, and
untruncated. Otherwise it raises: inspect `rt.observe(r)` or explicitly read a
fixed revision within a budget. Rows are positional; schema accompanies them.
Int64/UInt64/Decimal remain strings. Use Python integers/Decimal, and cast SQL
inputs to DECIMAL(38,0) (or the needed scale) before sums exceeding 64-bit range.

Compose dependent calculations and mechanical checks in one code call when the
next step is already known. Print only observations needed for the next decision.
On handoff, `rt.call('workspace', {'action':'summary','kind':'result','limit':10,
'max_bytes':8192})` returns items usable as bindings. Follow `next_cursor` unchanged;
stored catalog validity is not a fresh source scan.

Use `rt.call(method, params)` for asynchronous jobs, progressive analysis, paging,
export, and cancellation; discover an unfamiliar contract with `rt.schema(method)`.
An included observation needs no extra read. Partial checkpoints are exact only
for their stated prefix, and a completed query on one keeps partial coverage.
After a transport failure, reconnect and inspect durable state; never silently
replay a mutation. See [the full guide](agent-guide.md) for MCP and raw NDJSON.
