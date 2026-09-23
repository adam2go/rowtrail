# Verification: beta.2

[Home](../README.md) · [Analysis contracts](analysis.md) · [Current limits](progress.md)

Beta.2 adds native independent snapshots and optional stdlib analysis composition:
assertions, keyed diffs, durable SQL recipes and portable result branches. It adds
no external dependency. This page is being finalized during native release CI;
artifacts are published only after both native platforms and archive checks pass.

## Correctness gates

129 integration scenarios (113 existing + 16 beta.2) and 14 Rust tests, including
12 subprocess crash cases. New cases cover Parquet/result snapshot independence,
idempotency, bounded provenance, 2 KiB one-call observations, keyed comparison,
SQL/schema checks, partial rejection, durable recipes, package/import/recompute,
corruption/path/symlink/byte-limit rejection and retained partial failure records.

Formatting, Clippy, client/runtime dependency boundaries, real MCP/session/Rust
client contracts, out-of-core limits, install/checksum rejection and both bundled
demos are required. Pinned public alpha.8 and beta.1 archives exercise real one-way
metadata upgrades to schema 9, preserving old fixed results and partial checkpoints.
Older runtimes reject the upgraded store. Numeric provenance remains unchanged.

## Local measurements

Serial alternating beta.1 and beta.2 runs on one Apple arm64 Mac, macOS 26.6.2,
24 GiB RAM / 14 logical CPUs, Rust 1.94.0. Fresh workspaces; OS caches not flushed;
no concurrent local builds or timed workloads. Raw repeats and binary hashes are
retained. These are backend tests without external models, not adoption evidence.

The complete report and raw records are being prepared from the release build.
Small answer observation budgets, existing 2M-row follow-ups and 1M-row exploration,
cold/warm latency and the full new handoff workflow are measured separately.
Direct persistent DuckDB/DataFusion controls retain their intermediate tables.

## Native distribution

Unchanged budgets: 30,000,000 compressed bytes, 4,500,000 CLI bytes and 125,000,000
runtime bytes. Only passing native macOS arm64 / Ubuntu 22.04 x86_64 CI artifacts
are published; the same Linux archive is installed and tested on Ubuntu 24.04.
Archive hashes, bundled files, installed printed client/demos and all 557 dependency
notices must match independently before publication. Native CI remains pending.

## Limits

- Diff checks keys and performs multiple scans; bounded examples do not bound scan
  work. Modified rows cover only common identically typed non-key columns.
- Recipe/check/package helpers are Python composition, not native MCP tools. They
  add no dataframe library, model calls, scheduler or automatic retries.
- Checks save complete violations within execution limits. Partial, failed or
  nonfinal work cannot pass. Schema leakage checks use exact column names.
- Package checksums verify bytes, not authorship or business claims. Import does
  not execute SQL. Original recomputation requires explicit mapped inputs/run.
- Per-job budgets remain distinct from whole-workflow cost. Multi-step operations
  are not whole-workflow transactions; completed work survives later failures.
- Existing source invalidation and numeric contracts remain. No automatic legacy
  recertification, source snapshot filesystem guarantee, Windows or remote support.
- The latest paired-agent trial remains alpha.6: correct answers, but more time
  and cumulative input tokens than persistent DuckDB. No model-level gain claimed.

[Beta.1 report](releases/beta1-verification.md) ·
[Beta.1 native provenance](releases/beta1-verification.json) ·
[Design decision](decisions/010-portable-analysis.md).
