<div align="center">
  <img src="docs/brand/trail.png" width="720" alt="Trail, the RowTrail mascot: three mint data rows walking along a trail of orange stepping stones.">
  <h1>RowTrail</h1>
  <p><strong>Explore data. Keep the trail.</strong></p>
  <p>A small native data tool, built for agents.<br>Ask a question, keep an exact result, and continue from there.</p>
  <p>
    <a href="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml"><img src="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml/badge.svg" alt="Build and verify"></a>
    <a href="https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.6"><img src="https://img.shields.io/badge/release-v0.1.0--alpha.6-147D70" alt="Release v0.1.0-alpha.6"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-147D70" alt="Apache-2.0 license"></a>
  </p>
  <p><a href="README.zh-CN.md">简体中文</a> · <a href="#install">Install</a> · <a href="docs/agent-guide.md">Agent guide</a> · <a href="docs/verification.md">Test results</a> · <a href="CONTRIBUTING.md">Contribute</a></p>
</div>

---

An agent should be able to explore a large table without putting the whole table
in its context. RowTrail opens local CSV/TSV/Parquet, runs read-only SQL, and keeps
versioned results on disk. The agent gets a bounded, typed observation and can
branch from a saved result when the next question arrives.

**New in alpha.6:** smaller durable results, fewer publication costs, faster
bounded pages, and a shorter agent bootstrap. Alternating local comparisons show
about 14% faster million-row exploration and 60% faster 10K-row paging.
The native download budget stays at 30 MB.

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

**v0.1.0-alpha.6** is an early engineering preview, licensed under Apache-2.0.
Native packages are available for **macOS arm64** and **Linux x86_64**
(Ubuntu 24.04 / glibc 2.39 or newer).

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.6/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

The installer verifies SHA-256 and installs a versioned pair in `~/.local/bin`.
Set `ROWTRAIL_INSTALL_DIR` to choose another directory, or extract an archive
from [Releases](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.6).
Keep `rowtrail` and `rowtrail-runtime` together.

Verified native release sizes (decimal MB):

| Platform | Download `.tar.xz` | CLI | Runtime |
|---|---:|---:|---:|
| macOS arm64 | **19.16 MB** | 3.70 MB | 99.98 MB |
| Linux x86_64 | **22.42 MB** | 4.13 MB | 114.72 MB |

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

Five-query exploration, **seven alternating runs per version**, on one Apple
arm64 Mac. Median milliseconds; lower is better. Includes opening, materializing,
branching twice from a saved result, and returning to the original dataset.

| Entry | 16,384 rows | 1,048,576 rows |
|---|---:|---:|
| RowTrail alpha.5 · rerun | 109.46 | 348.38 |
| **RowTrail alpha.6 · persistent NDJSON** | **110.34** | **298.90** |
| DuckDB 1.5.5 · persistent session | 10.62 | 92.19 |
| Direct DataFusion 55.0.0 · persistent session | 4.76 | 59.05 |

Million-row exploration improves by about **14%**; small-input timing is essentially
unchanged. Direct engines remain faster: RowTrail pays for durability, content
verification, process isolation and protocol. Baselines retain in-memory tables;
direct DataFusion startup is excluded. Local timings are not universal guarantees.

- **Less integrity I/O:** the million-row saved result falls from 33.52 MB to
  **4.22 MB**, about 87% less, and from nine parts to six. Saved branches still
  verify stored content and read zero original-source bytes.
- **Faster pages:** 10,000-row fixed reads fall from **21.92 ms to 8.80 ms**,
  including CLI startup; medians of five runs.
- **Incompressible data checked:** high-entropy integer materialization plus three
  saved aggregates falls from 150.03 ms to **138.75 ms**, seven alternating runs.
- **Resource bounds exercised:** a million-row sort spills 26 times with a 32 MiB
  engine pool; every exported ID matches the independent oracle. Sampled worker
  RSS is about 139.7 MB. The engine pool is not an RSS limit.

Progressive observation remains useful: a single file with 16 row groups returns
its first prefix in **19.40 ms** and completes in **45.32 ms**, essentially unchanged
from the alpha.5 rerun. Ordinary SQL still wins when only the final answer matters.
[All conditions and raw records](docs/verification.md).

**Agent efficiency needs its own evidence.** The [minimal bootstrap](docs/agent-quickstart.md),
on-demand schemas and optional helpers keep full observations in caller code.
All 12 updated paired trials answer correctly and both arms reuse saved data on
handoff, but RowTrail is still slower and uses more cumulative input tokens than
persistent DuckDB. Compared with the historical pilot, exploration improves while
handoff does not; shorter instructions alone are insufficient.
[Every trial and limitation](docs/verification.md#real-external-agent-paired-pilot).

The local build and both native CI platforms pass **73 integration scenarios**, seven Rust tests including eight
subprocess commit-crash cases, and MCP/session/SDK/installation/two-version checks.
Same-size, same-mtime corruption of compressed parts is still rejected.

[Verification and timing conditions](docs/verification.md) ·
[Alpha.6 raw records](benchmarks/performance/alpha6/) ·
[Archived alpha.5 evidence](docs/releases/alpha5-verification.md)

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
