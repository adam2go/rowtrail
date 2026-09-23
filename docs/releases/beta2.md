# RowTrail 0.1.0-beta.2

An agent's analysis can become a reusable artifact: import cleaned Parquet,
keep an independent version, check assumptions, compare results, and hand a
branch to another workspace for follow-up or explicit recomputation.

- **Native snapshots:** copy datasets or completed exact results into independent
  managed Parquet. Retain labels and bounded origin/code/description. Shared by
  CLI, NDJSON, MCP and the Rust client; existing durability and quota paths remain.
- **Checks and comparisons:** optional stdlib Python SQL assertions, forbidden
  columns, primary-key diffs, exact counts and bounded examples. Duplicate/null
  keys block comparison; partial results cannot pass checks.
- **Reusable recipes:** named inputs, typed parameters, ordered SQL/check steps
  and a separate durable record for each run. Read-only idempotency lookup helps
  recover accepted work without replay.
- **Portable branches:** versioned manifest, Markdown report with bounded previews,
  SQL/parameters/dependencies/provenance, quality and complete Parquet payloads.
  Verify and import independently. SQL reruns only by explicit recipe invocation.
- **Small answers:** the 2 KiB scalar path returns its answer in one native call,
  preserving quality and prioritizing rows over optional metrics.

The higher-level checks/diff/recipe/package interfaces are initially **Python
composition**, not extra native MCP tools. Python uses only its standard library;
no dataframe engine, model SDK or scheduler is added to the product.

Local measurements on one Mac: 2 KiB scalar **2 → 1 calls**, **2,095 → 1,447 response
bytes**. Existing 2M-row follow-ups and 1M-row exploration remain broadly stable.
The new 131,072-row full handoff workflow takes **864 ms median** over five trials,
including verification/import/rerun. Direct persistent engines remain faster at
bare SQL. Raw repeats, outliers, counters and binary hashes are published; no
model-level time/token or adoption advantage is claimed.

**Verified native release:** macOS arm64 and Ubuntu 22.04 x86_64 each pass
**129 integration scenarios / 14 Rust tests**. The same Linux archive also passes
Ubuntu 24.04 installation and both demos. Downloads are **19.42 MB / 22.82 MB**,
with all 557 notices retained and no added dependency. Only native CI artifacts
are published, from source `1128826fae357cb7f9776b4606ba4194dd58ec3b`.
[CI and artifact provenance](../verification.md).

**Upgrade:** schema 9 is a one-way upgrade from 3..8. Stop old sessions/coordinators
before upgrading; keep a stopped full workspace copy for rollback. Old fixed
results, partial checkpoints and numeric provenance remain intact. Older runtimes
refuse upgraded stores.

**Limits:** keyed diff scans more than once and compares common identically typed
columns. Per-job budgets are not whole-workflow budgets. Packages/runs are not
multi-operation transactions. Checksums verify bytes, not authorship or business
claims. Import helper deadlines retain active staging inputs for explicit cleanup
after terminal state. No automatic retries/resume, scheduling, Windows or remote
sources are added.

[Complete guide](../analysis.md) · [Runnable demo](../../examples/analysis_quickstart.py) ·
[Verification and native artifact provenance](../verification.md) ·
[Raw measurements](../../benchmarks/performance/beta2/).
