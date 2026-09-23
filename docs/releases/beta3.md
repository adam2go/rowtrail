# RowTrail v0.1.0-beta.3

Spend context on evidence, and keep the analysis available for the next agent.

```sh
rowtrail demo --directory ./rowtrail-demo
```

The installed demo generates 100,000 orders with 64 columns, keeps a large
intermediate outside model context, verifies four exact totals and hands a
portable branch to a separate process. The recipient can continue without the
original CSV, explicitly recheck uniqueness and reproduce the aggregate using a
saved recipe. Reports state which missing original inputs prevent deeper
recomputation. Optional Python 3 standard library only; no network or model calls.

## Agent-facing changes

- Optional native compact responses via CLI `--compact`, NDJSON `response_mode`,
  MCP `_request.response_mode` and the stdlib client. Full quality/numeric policy,
  schema, fixed bindings, errors and truncation survive. Full diagnostics remain
  available at `control/status`; the default full response remains compatible.
- Search wide schemas by column-name substring without scanning the data.
  Context inspection retrieves purpose, SQL, parameters and fixed input bindings
  along with bounded fields and stored quality. Long definitions are explicitly
  omitted, never silently shortened into misleading SQL.
- `query(fetch=False)` retains an intermediate and returns zero data rows.
  Wait responses can include the answer; SQL checks reuse the included fixed
  revision row count. These avoid redundant reads/metadata calls.
- The demo, transcripts, decision cards and documentation show code composition
  with bounded evidence. No additional MCP tool or runtime dependency is added.

## Measured effects

Seven trials on one Mac, 100K-row workflow, `o200k_base`: response tokens
**4,753 → 3,438 (27.7% fewer)**. Including requests, the sum of reported medians
falls **22.2%**. A separate schema probe goes **1,060 → 127 tokens** when returning
the one relevant field instead of 64. These are measured JSON tokenizer counts,
not real-model billing or a new paired-agent experiment.

Direct DuckDB remains faster and returns less metadata. It is allowed both a
retained in-memory connection and a reopened durable database. RowTrail supplies
standard quality/version/context/handoff contracts that database applications can
also implement. Existing 2M-row follow-ups and 1M-row exploration remain broadly
stable, with a 0.2% slower follow-up median and 1.0% faster exploration median in the final series;
earlier series and regressions remain in the report.

[Full measurements, raw transcripts and limits](https://github.com/adam2go/rowtrail/blob/v0.1.0-beta.3/docs/verification.md) ·
[Demo guide](https://github.com/adam2go/rowtrail/blob/v0.1.0-beta.3/docs/agent-demo.md) · [Analysis composition](https://github.com/adam2go/rowtrail/blob/v0.1.0-beta.3/docs/analysis.md).

## Compatibility and release checks

Metadata stays at **schema 9**, compatible with beta.2 after stopping the old
coordinator. Native snapshots, FULL/fsync persistence, SHA-256 verification,
numeric policy and real cancellation are unchanged. Beta.1 and earlier upgrades
remain one-way. No automatic query replay or interrupted-run resume is introduced.

Release gates cover **143 integration scenarios and 14 Rust tests per native
platform**, published alpha.8/beta.1 upgrades, beta.2 old/new reader compatibility,
MCP/session/SDK, bounded out-of-core execution, installation and the bundled demo.
Only independently verified native macOS/Linux CI artifacts may be published;
the same Linux archive must install on Ubuntu 24.04. Distribution ceilings remain
**30 MB archive / 4.5 MB CLI / 125 MB runtime**.
