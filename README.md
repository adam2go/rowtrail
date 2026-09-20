<div align="center">
  <img src="docs/brand/trail.png" width="720" alt="Trail, the RowTrail mascot: three mint data rows walking along a trail of orange stepping stones.">
  <h1>RowTrail</h1>
  <p><strong>Explore data. Keep the trail.</strong></p>
  <p>A small native data tool, built for agents.<br>Ask a question, keep an exact result, and continue from there.</p>
  <p>
    <a href="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml"><img src="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml/badge.svg" alt="Build and verify"></a>
    <a href="https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.4"><img src="https://img.shields.io/badge/release-v0.1.0--alpha.4-147D70" alt="Release v0.1.0-alpha.4"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-147D70" alt="Apache-2.0 license"></a>
  </p>
  <p><a href="README.zh-CN.md">简体中文</a> · <a href="#install">Install</a> · <a href="docs/agent-guide.md">Agent guide</a> · <a href="docs/verification.md">Test results</a> · <a href="CONTRIBUTING.md">Contribute</a></p>
</div>

---

An agent should be able to explore a large table without putting the whole table
in its context. RowTrail opens local CSV/TSV/Parquet, runs read-only SQL, and keeps
versioned results on disk. The agent gets a bounded, typed observation and can
branch from a saved result when the next question arrives.

**New in alpha.4:** cumulative count/sum checkpoints and fixed-precision averages
over Parquet files. Inspect an early partial result, keep its revision, and choose whether
to continue. Profiles, explicit CSV preparation and safe workspace GC remain
available from alpha.3.

**Zero internal model calls. No API key. No spreadsheet UI.** Your agent chooses
the questions and decides when the evidence is sufficient.

| Built for | What the agent gets |
|---|---|
| **Observe progress honestly** | Exact file-prefix aggregates with explicit coverage and immutable revisions. |
| **Understand unfamiliar data** | On-demand null counts, min/max and exact top-k; every scan has budgets. |
| **Avoid repeated CSV parsing** | Explicit streaming preparation to an immutable Parquet dataset. |
| **Control stored data** | Pin/release, dependency-safe GC and managed-data quotas. |
| **Continue exploring** | Fixed dataset manifests and result revisions; reuse intermediate results through SQL. |
| **Spend context carefully** | Row and byte budgets, paginated observations, exact integer and Decimal representation. |
| **Work beyond one call** | Durable accepted jobs, explicit waiting, events and cancellation. |
| **Stay native and small** | Two executables; no Python, Node, Docker or external database required. |

```text
CSV / TSV / Parquet → exact query → saved result → next question
                          ↓              ↓
                    bounded observations for your agent
```

## Install

**v0.1.0-alpha.4** is an early engineering preview, licensed under Apache-2.0.
Native packages are available for **macOS arm64** and **Linux x86_64**
(Ubuntu 24.04 / glibc 2.39 or newer).

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.4/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

The installer verifies SHA-256 and installs a versioned pair in `~/.local/bin`.
Set `ROWTRAIL_INSTALL_DIR` to choose another directory, or extract an archive
from [Releases](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.4).
Keep `rowtrail` and `rowtrail-runtime` together.

Verified native release sizes (decimal MB):

| Platform | Download `.tar.xz` | CLI | Runtime |
|---|---:|---:|---:|
| macOS arm64 | **19.10 MB** | 3.65 MB | 99.88 MB |
| Linux x86_64 | **22.36 MB** | 4.10 MB | 114.60 MB |

CLI/runtime columns are uncompressed binaries. Budgets remain **30 MB** per
archive, 4.5 MB per CLI and 125 MB per runtime.
[Native artifact sizes and verification →](docs/verification.md#native-distribution)

## Give it a question

This example runs immediately after installation, with no data download:

```sh
rowtrail query --sql "SELECT region, SUM(amount) AS total
  FROM (VALUES ('east', 12), ('east', 8), ('west', 7)) AS orders(region, amount)
  GROUP BY region ORDER BY region"
```

The exact totals are `east = 20` and `west = 7`. The JSON response includes a
stable job reference and, when ready within the wait budget, a bounded result
observation. `ok: true` means acceptance; inspect `job.state` for completion.

For your own files, start with `rowtrail open ./orders.parquet`. Bind the returned
dataset and manifest IDs in SQL, then use a fixed result revision for the next
question. [The complete workflow](docs/usage.md#try-the-exact-exploration-loop)
covers opening, waiting, paging, branching and export. A
[runnable composition example](examples/explore.py) passes the references automatically.
The [alpha.3 end-to-end example](examples/prepare_explore.py) also profiles,
prepares, exports and collects its own intermediate data.
The [progressive example](examples/progressive.py) observes checkpoints and can
explicitly stop once it has enough file coverage.

## Connect your agent

| Entry | Use it for | Start here |
|---|---|---|
| CLI | Discovery and one-off calls | `rowtrail schema query` · `rowtrail doctor` |
| Persistent NDJSON | Many operations in one process | `rowtrail session` · [protocol guide](docs/agent-guide.md) |
| MCP over stdio | Agent tool hosts | `rowtrail --workspace /absolute/private/workspace mcp` |
| Rust client | Native programmatic composition | [`Client::session()` example](crates/client/examples/query.rs) |

All entries share the same contracts, jobs and result store. Closing a connection
does not cancel an accepted job. Transport errors never silently replay a
possibly accepted mutation. The optional [Python bridge](examples/session_client.py)
uses only the standard library; Python is not a product dependency.

## Measured, with the receipts

Five-query exploration, **median of five runs**, on one Apple arm64 Mac.
Milliseconds; lower is better. The workload includes opening, materializing,
branching twice from a saved result, and returning to the original dataset.

| Entry | 16,384 rows | 1,048,576 rows |
|---|---:|---:|
| RowTrail alpha.1 · CLI | 349.58 | 12,917.72 |
| RowTrail alpha.2 · CLI | 123.20 | 377.35 |
| **RowTrail alpha.3 · CLI** | **122.92** | **345.68** |
| RowTrail alpha.2 · persistent NDJSON | 104.13 | 360.55 |
| RowTrail alpha.3 · persistent NDJSON | 103.99 | 322.28 |
| **RowTrail alpha.4 · persistent NDJSON** | **104.03** | **319.67** |
| DuckDB 1.5.5 · persistent session | 9.39 | 92.23 |
| Direct DataFusion 55.0.0 · persistent session | 4.56 | 58.93 |

Alpha.3 includes content verification on saved-result queries. On the million-row
workload it is **8.4% faster through CLI / 10.6% through a session than alpha.2**
on this machine. Direct engines remain faster: RowTrail pays for durable results,
process isolation and protocol. They retain in-memory intermediates; direct
DataFusion startup is excluded. These are local measurements, not universal
performance guarantees or evidence of agent adoption.

**Preparation has a cost.** On a separate million-row CSV workload, conversion
costs 271 ms. Open + profile + ten follow-up aggregates takes 736 ms directly from
CSV, or 652 ms including preparation. The measured break-even is eight follow-ups;
it varies with data and queries. RowTrail leaves that choice to the agent.

**Earlier evidence has a cost.** On 16 Parquet files / 1,048,576 rows, progressive
aggregation returns its first observed partial checkpoint in **22.48 ms**, versus
**37.27 ms** for the ordinary SQL result. Final completion takes **189.46 ms versus
40.63 ms** because progressive mode persists every file checkpoint. Use ordinary
SQL when only a final answer is useful. Partials cover completed files only;
they are not population estimates. Average uses six decimal places with explicit
truncation; [types and limits](docs/decisions/004-progressive-file-aggregation.md).

The current suite has **53 integration scenarios**, six Rust tests including eight
subprocess commit-crash cases, and MCP/session/SDK/installation checks. Every
progressive checkpoint is checked against independent integer/Decimal arithmetic.
A million-row sort still spills under a 32 MiB engine pool and verifies every
exported ID. The pool budget is not a process RSS cap.

[Verification and all timing conditions](docs/verification.md) ·
[Alpha.4 raw records](benchmarks/performance/alpha4/) ·
[Archived alpha.3 evidence](docs/releases/alpha3-verification.md)

## What comes next

Progressive aggregation currently schedules whole files, with no GROUP BY or
filters. Row-group scheduling, sampling/estimates, interrupted-run continuation,
a SQL prepared-plan cache, native MCP Tasks, automatic host resume/eviction and
remote sources remain unimplemented. [Current capabilities and limits](docs/progress.md) are
tracked separately from the roadmap.

We want a useful tool that stays easy to install, compose and understand.
Contributions that make the contracts clearer, the package smaller, or a
complete exploration faster are especially welcome. Start with
[contributing](CONTRIBUTING.md), [building from source](docs/usage.md#build), or
[a reproducible issue](https://github.com/adam2go/rowtrail/issues).

<sub>Meet <a href="docs/brand/README.md">Trail / 小迹</a>, our three-row companion. One question, one result, one more step.</sub>
