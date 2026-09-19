# M0: dependency and execution verification

Measured on 2026-09-20, Apple arm64, 24 GiB memory, 14 logical CPUs.
Machine-specific raw numbers are in `benchmarks/m0.json`.

- Rust 1.94.0; DataFusion **and all DataFusion subcrates** 55.0.0;
  Arrow/Parquet 59.2.0; object_store 0.13.2; rmcp 3.4.0.
- Pinning only the top-level DataFusion crate initially selected 55.1.0
  subcrates. Exact internal constraints keep the researched family consistent.
- Arrow buffer's compatible range selected 59.3.0; explicitly constrained it.
- rmcp's InitializeResult is non-exhaustive and uses constructors/builders.
  The ordinary MCP probe negotiated 2025-11-25 and returned generated tool
  schemas. Native Tasks is deliberately not advertised until its adapter exists.
- DataFusion's ListingTableConfig requires listing options before schema.
  The wrong order causes a panic. Worker panic exits are supervised as failures.
- rusqlite 0.38 requires `fallible_uint` for checked u64/usize conversions.

The initial minimal release artifacts measured 2,999,632 bytes for the CLI and
96,190,752 bytes for the runtime. These are M0 probes, not final release sizes.
The dependency feature report records retained transitive modules. No claim is
made that top-level feature selection removes every unused function library.

The 1,048,576-row / 256-row-group Parquet probe completed a full sort under a
32 MiB engine MemoryPool with 81 spill operations. The plan reported approximately
421.8 MB of spill writes, including merge work. Process peak RSS was about 170 MB;
this demonstrates why the engine pool cannot be described as an RSS hard limit.

The CPU-heavy cancellation probe's nominal 200 ms in-process timer overshot by
several seconds. After dropping the stream, the subsequent 200 ms interval used
about 0.08 ms of CPU. The runtime therefore separates coordination from execution,
sends a cooperative termination signal, then forcibly kills and reaps the
exclusive worker after a 300 ms grace period. Cancellation is published only after
observing worker exit; successful jobs release their per-job execution context
and retain the idle process for the next job. No 200 ms universal cancellation guarantee is claimed.

Startup samples launch new CLI processes against a warm filesystem cache. They
are not cold-device measurements. The initial probe used one worker
per job. The current runtime reuses a worker after successful jobs and replaces it
after a cancellation, failure, or crash. Per-job session state and spill paths are
recreated; tests check that result branches use the same worker process.

Build on macOS can use an existing independent Command Line Tools installation
via the local helper. This does not change global Xcode settings or accept any
license. Linux builds use the ordinary C compiler and Cargo toolchain.

Reproduce:

```sh
./scripts/cargo-local.sh build --release --locked
./target/release/rowtrail-runtime probe --directory benchmarks/generated/m0 --rows 1048576
python3 scripts/mcp_probe.py target/release/rowtrail
```
