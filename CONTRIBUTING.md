# Contributing to RowTrail

RowTrail is an early, Apache-2.0 project for agent-driven local data exploration.
The aim is a small native tool with clear contracts and fast complete workflows.
Reproducible bug reports, better agent integrations, smaller distributions and
measured performance improvements are welcome.

Useful first contributions are a real agent task with a synthetic reproduction,
a small integration example, a confusing contract explained more clearly, or a
measurement showing where the complete workflow wastes time. The issue forms
capture the environment and evidence needed to reproduce a result.

Performance questions currently include small projected reads from large checked
result parts, preparation amortization on real CSV layouts, and label discovery
with much larger retained histories. Native targets need native verification,
size budgets and installation tests. No growth feature is more important than
correctness, clear observations and easy composition.

Read [current capabilities and limits](docs/progress.md), the
[agent guide](docs/agent-guide.md) and [engineering rules](AGENTS.md) before
changing behavior. [Build instructions](docs/usage.md#build) cover Rust 1.94.0 and
the native toolchain. No model API key is needed to develop or verify RowTrail.

## Find your way around

| Path | Responsibility |
|---|---|
| `crates/contracts` | Requests, responses and schemas shared by all entries |
| `crates/client` | Lightweight native client and persistent sessions |
| `crates/cli` | CLI, NDJSON session and MCP entry points |
| `crates/runtime` | Coordinator, workers, storage and query execution |
| `tests/integration` | Deterministic data, lifecycle and fault checks |
| `benchmarks` | Workload harnesses, measurements and size budgets |
| `docs` | Agent contracts, decisions, progress and verification evidence |

## Before sending a change

Keep a change focused and explain the concrete behavior before and after it.
Add a reproducible check when changing data or job semantics, and run the
[relevant verification commands](docs/verification.md#reproduce). Documentation
and artwork changes need link/render checks, not a fresh engine benchmark.

For a performance change, measure a complete exploration, including startup,
materialization, reuse and paging. Retain raw repeats, source commit, machine
conditions and executable hashes. Direct-engine baselines may keep a session and
intermediate tables; compare honestly. Do not trade away correctness or durability
for a better timing. See the [measurement method](benchmarks/README.md).

Preserve these contracts:

- Keep model calls, provider SDKs and a human spreadsheet UI out of the runtime.
- Keep the query engine out of the CLI/client dependency graph.
- Preserve exact types, fixed revisions and bounded observations.
- Keep accuracy, coverage, completion and output truncation distinct.
- A timeout while waiting is not cancellation. Cancellation must observe worker
  exit; accepted mutations must never be silently replayed after transport errors.
- Add no binary or archive growth without checking the distribution budgets.

## Reporting an issue

Include the RowTrail version, OS/architecture, a minimal input or fixture
recipe, the request/command, expected behavior and the actual JSON/error.
For performance reports, include workload size, timings and relevant conditions.
Remove private source paths and sensitive data from shared traces.

[Open an issue](https://github.com/adam2go/rowtrail/issues) or send a focused pull
request. New features should describe their impact on installation size, agent
contracts and execution cost. Contributions are licensed under
[Apache-2.0](LICENSE).
