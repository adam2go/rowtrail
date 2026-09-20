<div align="center">
  <img src="docs/brand/trail.png" width="720" alt="Trail, the RowTrail mascot: three mint data rows walking along a trail of orange stepping stones.">
  <h1>RowTrail</h1>
  <p><strong>Explore data. Keep the trail.</strong></p>
  <p>A small native data tool, built for agents.<br>Ask a question, keep an exact result, and continue from there.</p>
  <p>
    <a href="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml"><img src="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml/badge.svg" alt="Build and verify"></a>
    <a href="https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.2"><img src="https://img.shields.io/badge/release-v0.1.0--alpha.2-147D70" alt="Release v0.1.0-alpha.2"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-147D70" alt="Apache-2.0 license"></a>
  </p>
  <p><a href="README.zh-CN.md">简体中文</a> · <a href="#install">Install</a> · <a href="docs/agent-guide.md">Agent guide</a> · <a href="docs/verification.md">Test results</a> · <a href="CONTRIBUTING.md">Contribute</a></p>
</div>

---

An agent should be able to explore a large table without putting the whole table
in its context. RowTrail opens local CSV/TSV/Parquet, runs read-only SQL, and keeps
versioned results on disk. The agent gets a bounded, typed observation and can
branch from a saved result when the next question arrives.

**Zero internal model calls. No API key. No spreadsheet UI.** Your agent chooses
the questions and decides when the evidence is sufficient.

| Built for | What the agent gets |
|---|---|
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

**v0.1.0-alpha.2** is an early engineering preview, licensed under Apache-2.0.
Native packages are available for **macOS arm64** and **Linux x86_64**
(Ubuntu 24.04 / glibc 2.39 or newer).

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.2/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

The installer verifies SHA-256 and installs a versioned pair in `~/.local/bin`.
Set `ROWTRAIL_INSTALL_DIR` to choose another directory, or extract an archive
from [Releases](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.2).
Keep `rowtrail` and `rowtrail-runtime` together.

| Platform | Download, `.tar.xz` | Two installed executables |
|---|---:|---:|
| macOS arm64 | 19.00 MB | 103.17 MB |
| Linux x86_64 | 22.24 MB | 118.23 MB |

MB means decimal megabytes. Installed totals above count the binaries, excluding
notices and workspace data. [Exact sizes, checksums and CI provenance →](docs/verification.md#native-distribution)

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
| **RowTrail alpha.2 · CLI** | **123.20** | **377.35** |
| **RowTrail alpha.2 · persistent NDJSON** | **104.13** | **360.55** |
| DuckDB 1.5.5 · persistent session | 9.49 | 90.50 |
| Direct DataFusion 55.0.0 · persistent session | 4.79 | 57.55 |

The same CLI workload improved **2.84× / 34.23× over alpha.1**. Direct engines
remain faster in this test: RowTrail also pays for durable results, process
isolation and protocol. Reference engines retain in-memory intermediates;
DataFusion startup is excluded from its timing. These are local measurements,
not a universal speed claim or evidence of agent adoption.

The published binaries passed **30 integration checks and four Rust unit tests
on each platform**, plus MCP/session/SDK/installer probes. A **1,048,576-row sort**
spilled under a **32 MiB engine pool** and every exported ID matched an independent
sort. That pool limit is not a limit on total process memory.

[Verification report & test inventory](docs/verification.md) ·
[Method, paging and resource measurements](benchmarks/README.md) ·
[Raw repeated results](benchmarks/performance/summary.json)

## What comes next

The exact local exploration loop works today. Sampling, prepared execution,
native MCP Tasks, automatic host resume, retention/GC and remote sources are
still unimplemented. [Current capabilities and limits](docs/progress.md) are
tracked separately from the roadmap.

We want a useful tool that stays easy to install, compose and understand.
Contributions that make the contracts clearer, the package smaller, or a
complete exploration faster are especially welcome. Start with
[contributing](CONTRIBUTING.md), [building from source](docs/usage.md#build), or
[a reproducible issue](https://github.com/adam2go/rowtrail/issues).

<sub>Meet <a href="docs/brand/README.md">Trail / 小迹</a>, our three-row companion. One question, one result, one more step.</sub>
