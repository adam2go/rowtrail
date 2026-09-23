# Verification: beta.3

[Home](../README.md) · [Agent demo](agent-demo.md) · [Analysis contracts](analysis.md)

Beta.3 reduces operational context without changing stored evidence: optional
compact responses, schema-name search, contextual inspection, bounded wait
observations and an installed end-to-end demo. Metadata stays at schema 9; no
engine/client dependency is added. Local verification passes. Native macOS/Linux
CI and independent artifact verification are in progress for source
`5fa8df29e4ccaad5b6bde7e0e6382022a4df7941`.

[Native CI](https://github.com/adam2go/rowtrail/actions/runs/35892255644) ·
[Archived beta.2 evidence](releases/beta2-verification.md).

## Correctness gates

**143 integration scenarios** (129 existing + 14 beta.3) and **14 Rust tests**,
including 12 subprocess crash cases. The new suite exercises:

- Full/compact answer and numeric-quality equality on the same idempotent job.
- Diagnostic recovery, terminal SQL errors, output bounds and fixed row counts.
- Unicode/case-insensitive column search, original-offset pagination, no-match
  and explicit rejection of search-driven scanning.
- One-call purpose/SQL/dependency context and whole-definition omission.
- Zero-row observations, page cursors, truncated and partial-quality guards.
- One-call SQL assertions and bounded final answers returned by wait.
- External versus independent snapshot context, CLI/MCP preferences and the
  installed offline demo with separate-process import and explicit recomputation.

Formatting, Clippy, dependency boundaries, real MCP/session/Rust SDK, resource
spill, checksum rejection and installed examples remain required. The actual
published alpha.8 / beta.1 archives test their one-way upgrade to schema 9.
Published beta.2 tests unchanged schema 9, preserved fixed/partial results and
an old reader reopening a new-version result after the new coordinator stops.

## Context measurements

One Apple arm64 Mac, macOS 26.6.2, 24 GiB RAM / 14 logical CPUs, Rust 1.94.0.
Seven serial trials for each variant, rotated/reversed order, fresh workspaces or
databases, OS caches not flushed, no concurrent local builds or timed work.
All 100,000-row, 64-column answers pass an independent integer oracle.

| Shared workflow | Response tokens | Request tokens | Median ms |
|---|---:|---:|---:|
| RowTrail full | 4,706 | 986 | 72.69 |
| RowTrail compact | 3,461 | 1,043 | 72.14 |
| DuckDB live memory connection/tables | 223 | 325 | 18.12 |
| DuckDB durable file, reopened connection | 223 | 325 | 34.60 |

Tokens use `o200k_base`, tiktoken 0.12.0, each actual JSON message separately.
Compact saves **26.5% of response tokens**; the sum of request/response medians
saves **20.9%**. The preference itself adds request tokens. `cl100k_base` response
medians are 4,652 → 3,401 (26.9% fewer); UTF-8 bytes 14,587 → 10,521 (27.9% fewer).
A separate metadata probe returns 1,067 → 138 tokens by searching the one relevant
field instead of listing 64. Neither baseline is forced to list all fields.

These counts exclude system prompts, tool definitions, chat framing, reasoning,
caching and repeated context input. They are **not billed model usage** or a new
same-agent trial. All raw messages, encodings, versions and binary hashes are in
[the published records](../benchmarks/performance/beta3/). The code-composed compact
answer/check/follow-up card is 984 tokens; known mechanical work stays in code.
A database application can use code composition too.

Both engines save/reuse intermediates and aggregate before returning rows.
DuckDB is allowed a persistent connection and durable storage; its small SQL
wrapper does not return RowTrail's full quality, fixed references, task state or
lineage. RowTrail adds those common contracts at an observable cost. The benchmark
includes checks and reconnection; package export/import and recipient recomputation
are exercised separately by the demo. Native requests and SQL statements are not
external-model turns.

![Beta.3 measured context and SQL control costs](../benchmarks/performance/beta3/performance.svg)

## Existing workload regressions and stability

| Median unless stated | beta.2 | beta.3 |
|---|---:|---:|
| Save + ten follow-ups + reconnect, 2M rows | 683.78 ms | 689.54 ms |
| Save component | 271.97 ms | 275.51 ms |
| Ten follow-ups component | 347.80 ms | 347.66 ms |
| Five-query exploration, 1M rows | 144.62 ms | 146.23 ms |
| Cold startup | 18.46 ms | 18.55 ms |
| Warm scalar | 0.890 ms | 0.890 ms |
| Warm p95 | 1.354 ms | 1.329 ms |

There are seven alternating large-workload trials and eleven startup sessions
with 1,089 warm scalar samples per version. The larger tasks regress **0.8% /
1.1%**; no general engine speedup is claimed. A **2,276 ms beta.2 startup outlier**
is retained. These eleven samples cannot establish a tail-latency advantage.
Separate 1M-row controls remain faster: DuckDB 69.26 ms, direct DataFusion 36.83 ms
in the beta.3 series. Both retain in-memory intermediates.

## Installed agent demo

The actual local archive passes `rowtrail demo`, all older bundled examples and
installer rejection checks. A separate 100,000-row run returns four totals from
77,922 qualifying orders; all match an independent integer oracle. It searches
schema, saves intermediate rows without showing them, reconnects with purpose and
SQL, and packages a branch with bounded previews and explicit missing inputs.

After the demo deletes its own CSV, a separate process imports independent copies
and answers a follow-up. It then explicitly runs the supplied recipe: uniqueness
is checked again and the included intermediate reproduces the delivered totals.
The original filtering step cannot be recomputed without the missing input.
Import itself executes no saved SQL. The recipe's independent run record remains.

[Actual demo report](../benchmarks/performance/beta3/demo-report.md) ·
[Portable report](../benchmarks/performance/beta3/demo-handoff-report.md) ·
[Recipient evidence](../benchmarks/performance/beta3/demo-receiver.json).
Local directory prefixes are replaced with `<demo>` in those shared examples;
the tokenizer benchmark keeps its exact measured message strings.

## Native distribution

Budgets stay **30,000,000 archive / 4,500,000 CLI / 125,000,000 runtime bytes**.
The local archive is 19,484,908 bytes, CLI 3,942,144 bytes, runtime 101,061,328 bytes.
These are local measurements, not substitutes for native macOS/Linux release
artifacts. Final CI sizes/hashes and the Ubuntu 24.04 compatibility install will
be recorded before publication. All 557 dependency notices must match.

The CLI alone uses one codegen unit. A trial with `opt-level=s` increased macOS
CLI size to 4,445,456 bytes and was rejected; the selected CLI is smaller than the
local beta.2 baseline's 3,948,688 bytes. Runtime optimization is unchanged. The
optional demo is bundled as a script instead of duplicating it inside the CLI.
The benchmark-only tokenizer/database dependencies are never shipped.

## Remaining limits

- No new external-agent paired trial. The latest alpha.6 pilot remains 12 correct
  answers but more time and cumulative input tokens than persistent DuckDB.
- Compact retains full quality rather than replacing it with a blanket success
  flag. Source validity in metadata is stored state, not a fresh file check.
- Search is a name substring, not semantic understanding or a free data profile.
  Long caller-authored context is explicitly omitted and can be fetched separately.
- Query output/fetch limits do not bound total execution; compute/storage/time
  budgets remain separate. A fixed row count does not imply full source coverage.
- Recipes/packages are not whole-operation transactions, and diffs can scan more
  than once. No scheduler, automatic replay/resume or cross-job content cache.
- Checksums verify bytes, not authorship or business correctness. Imported SQL is
  inert; missing original inputs prevent complete recomputation.
- Engine memory is not an RSS hard limit. Existing numeric-contract boundaries,
  cancellation, FULL/fsync durability and per-job verification remain unchanged.

[Beta.2 evidence](releases/beta2-verification.md) ·
[Beta.1 evidence](releases/beta1-verification.md) · [Current limits](progress.md).
