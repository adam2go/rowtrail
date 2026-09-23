# A small answer, a reusable analysis

Verified 100,000 synthetic orders with an independent integer oracle. The
64-column input was searched by name; no full intermediate table was displayed.

| Response mode | Native calls | UTF-8 response bytes |
|---|---:|---:|
| Full | 8 | 12286 |
| Compact | 8 | 9219 |

Both runs execute the same SQL with the same row/output budgets. These are complete
native response envelopes, not estimated model tokens. Requests and all responses
are retained in the two transcript files. The bounded `agent-cards.json` is the
suggested model-facing output of code composition; raw events can stay on disk.

## What survives a handoff

- A new connection finds `channel revenue`, its purpose, SQL and fixed inputs.
- A reusable uniqueness check has zero violations and a durable evidence binding.
- [The portable report](handoff/report.md) includes SQL, quality and bounded previews.
- A separate process imports checked result files and answers without the original
  CSV. Imported SQL is inert. Missing original inputs are declared explicitly.
- The recipient explicitly runs `analysis-recipe.json` on the included eligible
  orders, verifies uniqueness again, and reproduces the four delivered totals.
  `receiver-run/run.json` retains this execution. The initial filter cannot be
  recomputed without the original input; the report does not claim otherwise.

## Continue using the actual workspace

```python
from rowtrail_client import RowTrail
with RowTrail('/Users/Adam/Documents/rowtrail/project/benchmarks/local/beta3/native-installed/rowtrail-0.1.0-beta.3-aarch64-apple-darwin/rowtrail', '<native-demo>/compact', response_mode='compact') as rt:
    saved = rt.find('channel revenue')
    print(rt.context(saved))
    answer = rt.query('SELECT channel, revenue_cents FROM t ORDER BY revenue_cents DESC LIMIT 1', {'t':saved})
    print(rt.observe(answer))
```

The original CSV was deliberately removed, so no new computation against that
input is available. The receiver workspace contains independent snapshots.

## Comparison boundary

A direct database can also return four rows, persist tables and export Parquet.
RowTrail adds a common interface for versions, quality, discovery, checks and
handoff. It does not make SQL inherently faster or universally use fewer tokens
than a minimal SQL wrapper. This scripted demonstration is not an agent trial;
see the repository benchmarks for repeated timings and tokenizer measurements.
