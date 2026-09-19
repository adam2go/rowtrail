# Repository and execution boundaries

Status: accepted for implementation, 2026-09-19.

The source repository is `project/` inside the local handoff directory. The
parent README, START_HERE, and parent docs are private development inputs, outside
this Git repository. Public usage documentation and implementation decisions are
written independently here. The project is Apache-2.0, as authorized by its owner.

The product is headless and performs no model calls. Only the runtime crate
depends on the query engine. The CLI, Rust client, and MCP share contracts and a
length-prefixed local Unix socket transport. macOS and Linux are initial targets.

Initial implementation combines the engine, sources, metadata, results, and
coordinator as modules in the runtime crate. One coordinator owns SQLite and
schedules one independent worker at a time. Worker execution and control requests
have separate lifetimes. A worker owns one job at a time, then can be reused; forced termination is isolated.

Object identity is persistent. A SQL binding fixes a manifest or result revision.
Result files must be durable before their metadata becomes visible. Completed
results remain reusable after the submitting command exits. Interrupting a worker
does not imply that arbitrary SQL can resume from the exact instruction stopped.

Resource counters distinguish logical byte requests, actual delivered source
bytes, result bytes, spill, and process memory. Engine accounting is not a total
RSS limit. Source file identity checks provide best-effort consistency, not an
immutable filesystem snapshot.
