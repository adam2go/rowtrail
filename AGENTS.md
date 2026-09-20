# RowTrail engineering rules

RowTrail is an agent-only, small native data tool. Keep these product priorities:

1. Agent-native contracts and programmatic composition. No spreadsheet GUI,
   model SDK, internal model call, provider account, or business-specific workflow.
2. A small native distribution. Keep engine dependencies out of client/CLI;
   preserve the two-executable boundary and enforce `benchmarks/budgets.json`.
3. Fast complete exploration. Measure startup, observations, intermediate-result
   persistence and reuse, paging, cancellation and large-data execution together.
   Direct DuckDB/DataFusion baselines may retain sessions and intermediate tables.

Read `docs/progress.md`, `docs/agent-guide.md` and the relevant decisions before
changing behavior. Accuracy, coverage, completion and output truncation are
separate. Immutable revisions, durable accepted jobs and true cancellation must
survive optimizations. Notifications are hints; committed storage is authoritative.
Never replay a possibly accepted mutation automatically after transport failure.

Use the pinned Rust toolchain and `Cargo.lock`. On macOS with independent Command
Line Tools, `scripts/cargo-local.sh` selects that toolchain for the command only;
it must not change the machine's global Xcode selection or accept licenses.

For runtime/client changes, run formatting, Clippy, workspace tests and relevant
release-binary integration tests. Core commands are in README. For performance
changes, retain repeated measurements and binary hashes, check original/result
I/O separately, and keep the independent correctness oracle. Timing results from
one machine are not universal speed guarantees or evidence of agent adoption.

Published native artifacts must come from passing macOS/Linux CI, pass size and
checksum checks, and retain Apache-2.0 and dependency notices. Do not replace old
release artifacts or claim unimplemented roadmap items are supported. Keep
progress and public setup examples consistent with the actual shipped interface.
