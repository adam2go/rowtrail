# RowTrail 0.1.0-beta.1 — keep asking, reuse the result

RowTrail is a small native data workspace for agents: open local CSV/TSV/Parquet,
ask read-only SQL questions, keep immutable results and return bounded typed
observations through CLI, persistent sessions or MCP. Apache-2.0, zero internal
model calls and no provider account.

This release moves the planned alpha.9 work into **beta.1**. It fixes numeric
correctness and reconnection issues reported during independent use, fills gaps
in the optional stdlib client, and reduces duplicate saved-result reads.

## Changes

- **Checked integer/Decimal SUM:** scalar, grouped, DISTINCT, sliding-window and
  partial-merge paths use checked wide state. Out-of-range final answers fail
  with `ARITHMETIC_OVERFLOW` and actionable details. Invalid Decimal precision is
  rejected before persistence, display and export, including old saved data.
- **Explicit numeric quality:** new work declares its policy and input ancestry;
  unknown legacy provenance stays unknown through further queries. Old fixed
  revisions are not rewritten or retroactively certified. Float SUM, AVG, scalar
  arithmetic and casts otherwise retain engine semantics.
- **Stable reconnection:** short private workspace sockets no longer depend on
  TMPDIR. Descriptor and live handshake validate workspace/store/PID/runtime and
  owner identity. Errors retain recovery details; failed connections do not replay
  possibly accepted jobs or kill old active work.
- **Small composable client:** the locally printable Python standard-library
  helper adds prepare/inspect/export, unique-label lookup, total-budget pagination
  with explicit resumable exhaustion, structured errors and lossless Python
  integer/Decimal conversion. No model SDK or new product dependency.
- **Less repeated result I/O:** saved IPC files skip format discovery and stay
  whole during scans; separate files remain parallel. SHA-256, the 8 MiB per-job
  cache, scan limits, durable commits and real cancellation remain in place.

## Measured benefits and costs

Seven alternating trials per version on one Apple arm64 Mac, macOS 26.6.2,
24 GiB RAM. Fresh workspaces; OS caches not flushed. The task saves a large
subset from 2,097,152 rows, runs ten follow-ups, reconnects and recovers the same
result. Python integer/Decimal arithmetic independently checks the answers.

| Median | alpha.8 | beta.1 |
|---|---:|---:|
| Complete task | 934.02 ms | **719.63 ms** |
| Ten saved-result queries | 567.27 ms | **375.18 ms** |
| Saved-data reads per follow-up | 82.03 MB | **28.16 MB** |
| Original-data reads per follow-up | 0 | 0 |

Complete time falls **23%**, follow-up time **34%**, saved-data reads **66%**.
First-save time rises **4.3%**, separate million-row exploration **4.0%**, and
response-envelope bytes **9.2%**. Warm-query median is 0.89 ms, but p95 rises from
1.39 to 3.38 ms. Direct engines remain faster; these deterministic backend tests
establish no model-level time or token advantage. Earlier failures and outliers
are retained, including a first-invocation startup outlier.

[Full methods, regressions and raw records](https://github.com/adam2go/rowtrail/blob/main/docs/verification.md).

## Native acceptance

Both macOS 14 arm64 and Ubuntu 22.04 x86_64 builds pass **113 integration scenarios
and 14 Rust tests**, including twelve subprocess crash cases. Formatting, Clippy,
dependency boundaries, MCP/session/SDK, real alpha.8 upgrades, out-of-core sort,
installer rejection checks, SHA-256 and all 557 notices pass. The same Linux
archive installs and runs the demo on Ubuntu 24.04.

| Native target | Download | CLI | Runtime |
|---|---:|---:|---:|
| macOS arm64 | **19.37 MB** | 3.82 MB | 100.96 MB |
| Linux x86_64 | **22.71 MB** | 4.33 MB | 115.90 MB |

All remain inside 30 / 4.5 / 125 MB budgets. Downloads grow only 0.165 / 0.113 MB
from alpha.8. No external product dependency was added.

[Passing CI](https://github.com/adam2go/rowtrail/actions/runs/35757844420) uses source
`cbde5d1556dacf7506e9afb3e57083183eb3f927`.
[Independent artifact provenance](https://github.com/adam2go/rowtrail/blob/v0.1.0-beta.1/docs/release-verification.json).
The tag adds measured results and release documentation without changing the
verified executable bytes.

## Install and upgrade

Native packages support **macOS arm64** and **Linux x86_64, glibc 2.35+**.
Keep the CLI and runtime together. Python is only needed for optional examples.

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-beta.1/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

**Workspace metadata upgrades to schema 8.** Close old sessions and allow the
old coordinator to exit. Retain a stopped full pre-upgrade workspace copy if
rollback is needed; older runtimes refuse upgraded stores. Old wrapped integers
cannot be recovered and retain unknown provenance. Invalid old Decimals fail
explicitly. Read the [numeric contract](https://github.com/adam2go/rowtrail/blob/v0.1.0-beta.1/docs/numeric-contract.md).

Beta means the bounded local exploration workflow has passed its acceptance
gates. It does not promise stable 1.0 metadata, arbitrary-precision SQL, signed
packages, Windows, remote sources or automatic continuation after computation
failure. Progressive aggregation still excludes GROUP BY and filters.

Contributions are welcome: real agent workflows, confusing contracts, warm-query
tails, smaller observations or binaries, and independently reproducible results.
[Start here](https://github.com/adam2go/rowtrail/blob/main/CONTRIBUTING.md).
