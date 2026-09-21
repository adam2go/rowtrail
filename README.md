<div align="center">
  <img src="docs/brand/trail.png" width="720" alt="Trail, the RowTrail mascot: three mint data rows walking along a trail of orange stepping stones.">
  <h1>RowTrail</h1>
  <p><strong>Explore data. Keep the trail.</strong></p>
  <p>A small native data tool, built for agents.<br>Ask a question, keep an exact result, and continue from there.</p>
  <p>
    <a href="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml"><img src="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml/badge.svg" alt="Build and verify"></a>
    <a href="https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.5"><img src="https://img.shields.io/badge/release-v0.1.0--alpha.5-147D70" alt="Release v0.1.0-alpha.5"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-147D70" alt="Apache-2.0 license"></a>
  </p>
  <p><a href="README.zh-CN.md">简体中文</a> · <a href="#install">Install</a> · <a href="docs/agent-guide.md">Agent guide</a> · <a href="docs/verification.md">Test results</a> · <a href="CONTRIBUTING.md">Contribute</a></p>
</div>

---

An agent should be able to explore a large table without putting the whole table
in its context. RowTrail opens local CSV/TSV/Parquet, runs read-only SQL, and keeps
versioned results on disk. The agent gets a bounded, typed observation and can
branch from a saved result when the next question arrives.

**New in alpha.5:** row-group checkpoints inside a single Parquet file, faster
progressive aggregation, and a bounded workspace catalog for reconnecting agents.
`rowtrail guide` explains the workflow; `mcp-config` prints a ready-to-copy config.

**Zero internal model calls. No API key. No spreadsheet UI.** Your agent chooses
the questions and decides when the evidence is sufficient.

| Built for | What the agent gets |
|---|---|
| **Observe progress honestly** | Exact row-group-prefix aggregates with explicit coverage and immutable revisions. |
| **Understand unfamiliar data** | On-demand null counts, min/max and exact top-k; every scan has budgets. |
| **Avoid repeated CSV parsing** | Explicit streaming preparation to an immutable Parquet dataset. |
| **Control stored data** | Pin/release, dependency-safe GC and managed-data quotas. |
| **Continue exploring** | Discover saved bindings with `workspace summary`; reuse fixed results through SQL. |
| **Spend context carefully** | Row and byte budgets, paginated observations, exact integer and Decimal representation. |
| **Work beyond one call** | Durable accepted jobs, explicit waiting, events and cancellation. |
| **Stay native and small** | Two executables; no Python, Node, Docker or external database required. |

```text
CSV / TSV / Parquet → exact query → saved result → next question
                          ↓              ↓
                    bounded observations for your agent
```

## Install

**v0.1.0-alpha.5** is an early engineering preview, licensed under Apache-2.0.
Native packages are available for **macOS arm64** and **Linux x86_64**
(Ubuntu 24.04 / glibc 2.39 or newer).

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.5/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

The installer verifies SHA-256 and installs a versioned pair in `~/.local/bin`.
Set `ROWTRAIL_INSTALL_DIR` to choose another directory, or extract an archive
from [Releases](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.5).
Keep `rowtrail` and `rowtrail-runtime` together.

Verified native release sizes (decimal MB):

| Platform | Download `.tar.xz` | CLI | Runtime |
|---|---:|---:|---:|
| macOS arm64 | **19.09 MB** | 3.70 MB | 99.98 MB |
| Linux x86_64 | **22.42 MB** | 4.13 MB | 114.77 MB |

CLI/runtime are uncompressed sizes. Budgets remain **30 MB** per archive,
4.5 MB per CLI and 125 MB per runtime. [Native artifact verification](docs/verification.md#native-distribution).

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
explicitly stop once it has enough fragment or file coverage.

## Connect your agent

| Entry | Use it for | Start here |
|---|---|---|
| CLI | Discovery and one-off calls | `rowtrail guide` · `rowtrail schema query` · `rowtrail doctor` |
| Persistent NDJSON | Many operations in one process | `rowtrail session` · [protocol guide](docs/agent-guide.md) |
| MCP over stdio | Agent tool hosts | `rowtrail --workspace /absolute/private/workspace mcp-config` |
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
| RowTrail alpha.4 · persistent NDJSON (historical) | 104.03 | 319.67 |
| **RowTrail alpha.5 · persistent NDJSON** | **104.35** | **318.97** |
| DuckDB 1.5.5 · persistent session | 10.82 | 88.98 |
| Direct DataFusion 55.0.0 · persistent session | 4.78 | 55.14 |

Ordinary exploration performance is stable versus alpha.4 in these samples.
Direct engines remain faster: RowTrail pays for durable results, content verification,
process isolation and protocol. Baselines retain in-memory intermediates; direct
DataFusion startup is excluded. These are local timings, not universal guarantees.

**Preparation has a cost.** In the historical alpha.3 million-row CSV experiment, conversion
costs 271 ms. Open + profile + ten follow-up aggregates takes 736 ms directly from
CSV, or 652 ms including preparation. The measured break-even is eight follow-ups;
it varies with data and queries. RowTrail leaves that choice to the agent.

**Progressive aggregation is substantially cheaper in alpha.5.** On 16 Parquet
files / 1,048,576 rows, its first checkpoint arrives in **21.22 ms**, and final
completion in **49.18 ms** (alpha.4 rerun: 184.11 ms). The default now coalesces
intermediate checkpoints; this case writes two instead of 16. Ordinary SQL still
finishes sooner at **37.60 ms**. A single file with 16 row groups returns its first
prefix in **20.18 ms** and completes in **46.20 ms**. Prefixes describe processed
rows, never population estimates. [Conditions and raw measurements](docs/verification.md).

**Real agent evidence, including the gap.** A same-model paired pilot completes
all 12 exploration/handoff tasks correctly. Both RowTrail and persistent DuckDB
reuse saved data without rescanning the original during handoff. RowTrail is slower
and uses more cumulative input tokens in this small pilot; it does not yet show
better end-to-end agent efficiency. [All trials and limitations](docs/verification.md#real-external-agent-paired-pilot).

The current suite has **65 integration scenarios**, six Rust tests including eight
subprocess commit-crash cases, and MCP/session/SDK/installation checks. Every tested
row-group checkpoint matches independent integer/Decimal arithmetic.
A million-row sort still spills under a 32 MiB engine pool and verifies every
exported ID. The pool budget is not a process RSS cap.

[Verification and all timing conditions](docs/verification.md) ·
[Alpha.5 raw records](benchmarks/performance/alpha5/) ·
[Archived alpha.4 evidence](docs/releases/alpha4-verification.md)

## What comes next

Progressive aggregation supports whole files and row groups, with no GROUP BY or
filters. Sampling/estimates, interrupted-run continuation,
a SQL prepared-plan cache, native MCP Tasks, automatic host resume/eviction and
remote sources remain unimplemented. [Current capabilities and limits](docs/progress.md) are
tracked separately from the roadmap.

We want a useful tool that stays easy to install, compose and understand.
Contributions that make the contracts clearer, the package smaller, or a
complete exploration faster are especially welcome. Start with
[contributing](CONTRIBUTING.md), [building from source](docs/usage.md#build), or
[a reproducible issue](https://github.com/adam2go/rowtrail/issues).

<sub>Meet <a href="docs/brand/README.md">Trail / 小迹</a>, our three-row companion. One question, one result, one more step.</sub>
