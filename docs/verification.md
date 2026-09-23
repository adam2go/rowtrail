# Verification: beta.3

[Home](../README.md) · [Agent demo](agent-demo.md) · [Analysis contracts](analysis.md)

Beta.3 reduces operational context without changing stored evidence: optional
compact responses, schema-name search, contextual inspection, bounded wait
observations and an installed end-to-end demo. Metadata stays at schema 9; no
engine/client dependency is added. Both native platforms and independent artifact
verification pass for source `3c7c6e2b8e6f9471f6addbc25fe8892889376878`.
[Machine-readable artifact provenance](release-verification.json).

[Native CI](https://github.com/adam2go/rowtrail/actions/runs/35898544833) ·
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
The timing/token series uses local source d512eea; the subsequent installation-link
fix changes only demo resource lookup. Query/response code and runtime are unchanged;
[an additional latency repeat](../benchmarks/performance/beta3/post-link-fix-latency.json)
checks the final local CLI. All reports carry their own binary hashes.

| Shared workflow | Response tokens | Request tokens | Median ms |
|---|---:|---:|---:|
| RowTrail full | 4,753 | 998 | 73.28 |
| RowTrail compact | 3,438 | 1,038 | 71.88 |
| DuckDB live memory connection/tables | 223 | 324 | 17.44 |
| DuckDB durable file, reopened connection | 223 | 324 | 34.38 |

Tokens use `o200k_base`, tiktoken 0.12.0, each actual JSON message separately.
Compact saves **27.7% of response tokens**; the sum of request/response medians
saves **22.2%**. The preference itself adds request tokens. `cl100k_base` response
medians are 4,703 → 3,386 (28.0% fewer); UTF-8 bytes 14,615 → 10,521 (28.0% fewer).
A separate metadata probe returns 1,060 → 127 tokens by searching the one relevant
field instead of listing 64. Neither baseline is forced to list all fields.

These counts exclude system prompts, tool definitions, chat framing, reasoning,
caching and repeated context input. They are **not billed model usage** or a new
same-agent trial. All raw messages, encodings, versions and binary hashes are in
[the published records](../benchmarks/performance/beta3/). The code-composed compact
answer/check/follow-up card is 986 tokens; known mechanical work stays in code.
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
| Save + ten follow-ups + reconnect, 2M rows | 698.81 ms | 700.08 ms |
| Save component | 278.85 ms | 285.96 ms |
| Ten follow-ups component | 350.53 ms | 349.30 ms |
| Five-query exploration, 1M rows | 146.32 ms | 144.91 ms |
| Cold startup | 18.40 ms | 18.60 ms |
| Warm scalar | 0.887 ms | 0.891 ms |
| Warm p95 | 1.304 ms | 1.310 ms |

There are seven alternating large-workload trials and eleven startup sessions
with 1,089 warm scalar samples per version. Follow-ups are **0.2% slower** and
exploration **1.0% faster** in this series; no general engine speedup is claimed.
The [earlier complete series](../benchmarks/performance/beta3/earlier/)
retains a **2,276 ms beta.2 startup outlier**; the final repeat has no comparable
outlier. These eleven samples cannot establish a tail-latency advantage.
Separate 1M-row controls remain faster: DuckDB 69.70 ms, direct DataFusion 37.13 ms
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
Published archives come from the passing native CI run above. Downloaded archive
hashes and embedded binaries match the native test reports, all 557 dependency
notices and bundled client/guides match source, and the same Linux archive installs
on Ubuntu 24.04. The downloaded macOS archive also passes the beta.3 suite and
installed demos again locally.

| Native CI artifact | Compressed bytes | CLI bytes | Runtime bytes |
|---|---:|---:|---:|
| macOS arm64 | 19,430,384 | 3,777,792 | 101,028,320 |
| Linux x86_64 | 22,876,268 | 4,452,544 | 116,030,120 |

Exact SHA-256 values and complete per-platform reports are in
[release-verification.json](release-verification.json). The separate local benchmark
build is 3,761,136 CLI / 101,061,328 runtime bytes; its package was 19,456,692 bytes.
Archive documents reflect the verified source; later report/README updates on the
release tag do not change these executable bytes.

The CLI alone uses one codegen unit and `opt-level=2`. A trial with
`opt-level=s` increased macOS CLI size to 4,445,456 bytes and was rejected.
At `opt-level=3`, Linux exceeded the unchanged 4,500,000-byte CLI ceiling
(4,616,672 bytes), despite passing the functionality suites. The final configuration
is remeasured above and keeps the engine at its previous optimization level.
The local CLI is smaller than beta.2's 3,948,688 bytes. The
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

The first complete native candidate was rejected after a local installed-symlink
demo failed despite CI passing. The CI source directory had masked the missing
resource lookup. Executable canonicalization and a regression using a distinct
bundled script fix this. [Rejected candidate evidence](../benchmarks/performance/beta3/rejected-install-candidate.json).
