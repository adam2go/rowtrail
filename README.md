<div align="center">
  <img src="docs/brand/trail.png" width="720" alt="Trail, the RowTrail mascot: three mint data rows walking along a trail of orange stepping stones.">
  <h1>RowTrail</h1>
  <p><strong>Explore data. Keep the trail.</strong></p>
  <p>A small native data tool, built for agents.<br>Ask a question, keep an exact result, and continue from there.</p>
  <p>
    <a href="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml"><img src="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml/badge.svg" alt="Build and verify"></a>
    <a href="https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.3"><img src="https://img.shields.io/badge/release-v0.1.0--alpha.3-147D70" alt="Release v0.1.0-alpha.3"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-147D70" alt="Apache-2.0 license"></a>
  </p>
  <p><a href="README.zh-CN.md">简体中文</a> · <a href="#install">Install</a> · <a href="docs/agent-guide.md">Agent guide</a> · <a href="docs/verification.md">Test results</a> · <a href="CONTRIBUTING.md">Contribute</a></p>
</div>

---

An agent should be able to explore a large table without putting the whole table
in its context. RowTrail opens local CSV/TSV/Parquet, runs read-only SQL, and keeps
versioned results on disk. The agent gets a bounded, typed observation and can
branch from a saved result when the next question arrives.

**New in alpha.3:** profile selected columns, explicitly prepare CSV/TSV as
Parquet, and release workspace data safely. Saved-result queries and exports
now verify content integrity as well as file identity.

**Zero internal model calls. No API key. No spreadsheet UI.** Your agent chooses
the questions and decides when the evidence is sufficient.

| Built for | What the agent gets |
|---|---|
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

**v0.1.0-alpha.3** is an early engineering preview, licensed under Apache-2.0.
Native packages are available for **macOS arm64** and **Linux x86_64**
(Ubuntu 24.04 / glibc 2.39 or newer).

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.3/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

The installer verifies SHA-256 and installs a versioned pair in `~/.local/bin`.
Set `ROWTRAIL_INSTALL_DIR` to choose another directory, or extract an archive
from [Releases](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.3).
Keep `rowtrail` and `rowtrail-runtime` together.

| Platform | Download `.tar.xz` | Two installed binaries |
|---|---:|---:|
| macOS arm64 | 19.03 MB | 103.40 MB |
| Linux x86_64 | 22.31 MB | 118.49 MB |

Both native CI jobs passed. MB is decimal; installed sizes exclude notices and
workspace data. The compressed budget remains 30 MB.
[Exact sizes, checksums and CI provenance →](docs/verification.md#native-distribution)

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
| **RowTrail alpha.3 · persistent NDJSON** | **103.99** | **322.28** |
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

Alpha.3 adds **13 new integration scenarios** to the original 30, plus crash
injection at four commit boundaries for both Arrow results and prepared Parquet.
Six Rust tests include the subprocess harness. A **1,048,576-row sort** spills
under a **32 MiB engine pool**, and every exported ID is checked against an
independent sort. That pool budget does not cap total process memory.

[Verification report & test inventory](docs/verification.md) ·
[Method, paging and resource measurements](benchmarks/README.md) ·
[Alpha.3 raw performance records](benchmarks/performance/alpha3/)

## What comes next

The next slice is restricted progressive Parquet aggregation. Sampling and
estimates, a SQL prepared-plan cache, native MCP Tasks, automatic host resume,
automatic storage eviction and remote sources remain unimplemented. [Current capabilities and limits](docs/progress.md) are
tracked separately from the roadmap.

We want a useful tool that stays easy to install, compose and understand.
Contributions that make the contracts clearer, the package smaller, or a
complete exploration faster are especially welcome. Start with
[contributing](CONTRIBUTING.md), [building from source](docs/usage.md#build), or
[a reproducible issue](https://github.com/adam2go/rowtrail/issues).

<sub>Meet <a href="docs/brand/README.md">Trail / 小迹</a>, our three-row companion. One question, one result, one more step.</sub>
