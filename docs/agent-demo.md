# One command, a reusable analysis

```sh
rowtrail demo --directory ./rowtrail-demo
```

The installed beta.3 executable includes this demo. It needs Python 3's standard
library, generates 100,000 synthetic orders locally, and performs **zero network
requests or model calls**. No DuckDB/Pandas/Polars installation is needed. Native
RowTrail queries do not require Python. Omit `--directory` to use a new temporary
directory; use `--rows 1000000` for a larger fixture (32..1,000,000). Existing
directories are rejected instead of overwritten.

The demo behaves like an agent's code environment: it retains full state and
prints a bounded answer and evidence paths. It is deterministic programmatic
composition, not a claim that an external model solved the task.

## The task

**Which channels produce revenue after excluding cancelled orders and bots? Can
another caller verify the saved evidence and ask a new question?**

1. Open a 64-column table without printing all columns. Search schema names for
   revenue, then inspect only the fields needed for the next SQL step.
2. Save qualifying orders with a label and purpose. `fetch=False` returns zero
   data rows; the complete fixed result remains available for SQL.
3. Return four channel totals, retaining types, exactness, coverage, numerical
   policy and truncation. Verify every value with an independent integer oracle.
4. Reconnect using only the workspace and label. One context inspection recovers
   the SQL, purpose and fixed input bindings; continue with a small aggregation.
5. Execute a reusable unique-order-ID assertion and save its violation evidence.
6. Export a portable report/result branch, delete the **demo's own** original CSV,
   and import into a separate process/workspace. Ask one explicit new question.
   Import verifies/copies payloads; it does not execute the saved SQL.
7. Explicitly run the supplied JSON recipe on the included eligible-order result:
   recheck uniqueness and reproduce the delivered totals. The original filtering
   step cannot be recomputed without its missing raw input; that limit is stated.

`report.md` explains the workflow, `handoff/report.md` is the portable report,
`agent-cards.json` contains bounded model-facing evidence, and the two transcript
files contain all shared-workflow requests/responses. `receiver.json` retains the
recipient's answer and verification scope. `result.json` contains the summary.
`analysis-recipe.json` and `receiver-run/run.json` retain the explicit rerun.
The workspaces remain available for follow-up; normal idle shutdown still applies.

## What actually saves context

Use a compact session for model-facing calls:

```python
from rowtrail_client import RowTrail  # rowtrail python-client > rowtrail_client.py

with RowTrail(workspace='.rowtrail', response_mode='compact') as rt:
    data = rt.open('orders.parquet', output={'max_rows':0, 'max_bytes':2048})
    print(rt.inspect(data, search='revenue'))  # metadata, no row scan
    saved = rt.query('SELECT order_id, channel, revenue_cents FROM t WHERE is_bot=0',
                     {'t':data}, label='eligible orders', fetch=False,
                     provenance={'description':'Exclude bot orders; amounts in cents.'})
    answer = rt.query('SELECT channel, SUM(revenue_cents) FROM t GROUP BY channel',
                      {'t':saved}, execution={'output':{'max_rows':10,'max_bytes':4096}})
    print(rt.observe(answer))
```

`fetch=False` limits observation only; it does not limit computation or saved
result size. Leave the large `saved` response in code. Print the few answers
needed for reasoning. Discover a schema only for unfamiliar operations; do not
repeatedly print the whole guide, `doctor`, plans or raw job metrics.

CLI: `rowtrail --compact query ...`. NDJSON: add
`"response_mode":"compact"` to the request envelope. MCP: use
`rowtrail --compact mcp-config` for the server preference, or
`_request: {"response_mode":"compact"}` for one tool call. A per-call MCP `full`
overrides that server preference. This adds no new MCP tools.

Compact removes repeated references, job timestamps/metrics and read metrics.
It preserves answer schema/rows, full quality including numerical semantics,
validity, job state/error, fixed bindings, row count, cursors and truncation.
`details_omitted: true` signals that operational details were omitted.
`control/status` always returns full job diagnostics, even in a compact session.
`control/wait` optionally accepts `output: {max_rows, max_bytes}` and can include
the result directly, avoiding a separate read. A completed check uses the fixed
revision's included row count instead of issuing a redundant metadata inspection.

`inspect` accepts `search` for schema/context/provenance metadata only. Matching
is a Unicode-lowercased substring on the name, not regex or semantic search.
`matched_field_count` describes all matches; `next_field_offset` remains the
original schema index and is used with the same filters. A search does not
silently select columns for an expensive profile. `context()` includes stored
purpose/SQL/parameters/inputs when they fit; long context is omitted explicitly.
It never scans sources or certifies that caller-authored notes are correct.

## What a direct database can already do

DuckDB can keep connections and intermediate tables, persist a database, project
columns, aggregate before returning rows, and export Parquet. Those are valid
baselines. RowTrail's advantage is packaging additional behavior as consistent
agent contracts: fixed references and quality, discoverable purpose/dependencies,
durable task state, reusable checks, and portable handoffs with declared limits.
A database application can implement these too; they are additional application
work, not impossible database capabilities.

The full/compact demo runs use identical SQL, schemas and output budgets. The
report measures UTF-8 transport bytes. The optional maintainer benchmark measures
named tokenizer encodings and a persistent DuckDB control; neither is an invoice
for real-model tokens. RowTrail can use more context and time than a minimal SQL
wrapper because it returns and persists more guarantees. No universal saving or
SQL-speed advantage is claimed.

Result-only packages declare missing original inputs. Recipients can verify
included bytes and query the results; recomputing absent originals is unavailable.
Checksums do not prove authenticity or business correctness. Schema 9 is unchanged
from beta.2; finish/stop the old coordinator before switching executable versions.
