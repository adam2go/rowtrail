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
                 label='non-null rows',
                 execution={'output': {'max_rows': 0, 'max_bytes': 8192}})
next_result = rt.query('SELECT SUM(amount) FROM saved', {'saved': saved})
print(rt.observe(next_result))
```

Use SQL appropriate to the discovered fields. `query(sql, bindings={},
parameters=[], execution={}, timeout=30, idempotency_key=None, label=None)` accepts an open,
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
On handoff, `rt.call('workspace', {'action':'summary','kind':'result',
'label':'non-null rows','max_bytes':8192})` returns matching fixed bindings,
row counts and bounded field hints. Labels are descriptive, not unique; choose
the intended item. Follow `next_cursor` with the same filter. Omitted fields need
explicit inspection; stored validity is not a fresh source scan.

Use `rt.call(method, params)` for asynchronous jobs, progressive analysis, paging,
export, and cancellation; discover an unfamiliar contract with `rt.schema(method)`.
An included observation needs no extra read. Partial checkpoints are exact only
for their stated prefix, and a completed query on one keeps partial coverage.
After a transport failure, reconnect and inspect durable state; never silently
replay a mutation. See [the full guide](agent-guide.md) for MCP and raw NDJSON.

Beta.1 adds `rt.find(label)` for a strict unique saved-result lookup; it rejects
missing/duplicate/expired/nonfinal matches. `prepare`/`inspect`/`export` compose
without extra dependencies. `pages` requires total rows, response bytes and pages;
exhaustion before EOF is a resumable error. `RowTrailError` retains structured
code/details and the response. See [the full helper contract](agent-guide.md).

Integer/Decimal SQL SUM now checks overflow; Decimal output precision is guarded.
Other SQL arithmetic retains engine semantics. `accuracy=exact` describes sampling,
not arbitrary-precision arithmetic. Old results without numeric policy are unknown;
[numeric policy v1](numeric-contract.md) explains the scope and legacy behavior.

## Independent versions and reusable analysis (beta.2)

Native `snapshot` accepts a dataset or final exact complete result binding and
streams it into independent managed Parquet. `label` and bounded `provenance`
(origin, description, code) remain discoverable; code is never executed. Get the
contract with `rowtrail schema snapshot` or use MCP `data_snapshot`.
`control` action `lookup`, `ref: idempotency_key`, reads committed acceptance
without replaying a mutation. Missing is only a point-in-time observation.

The printed stdlib client adds `from_parquet`, `snapshot`, `scalar`, `records`,
`check`, `diff`, `run_recipe`, `pack`, `verify_package` and `import_package`.
Higher-level composition is initially Python-only. Checks cannot pass on partial
coverage. Diffs require unique non-null keys. Packages are checked directories,
not signed attestations; SQL reruns only through an explicit recipe invocation.
[Contracts, examples, budgets and verification boundaries](analysis.md).

Stored validity is not a fresh original-source check: inspecting metadata does
not scan files; saved-result use checks its parts. Original-source operations
may discover changes and invalidate dependents. Take an independent snapshot
before that point when retaining a standalone version is intended.
