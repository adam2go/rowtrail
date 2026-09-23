# Introducing RowTrail beta.2

Draft launch copy; the user publishes social posts. Link the
[release](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-beta.2),
[analysis guide](analysis.md) and [verification report](verification.md).

## Short announcement

An agent's analysis should survive the conversation.

RowTrail beta.2: bring cleaned Parquet, save an independent snapshot, check your
assumptions, compare versions, and hand off a branch with SQL + results + a report.
Rerun it next week as a recipe.

Native CLI + MCP; optional stdlib Python composition. Zero internal model calls.
Apache-2.0. Help us test real workflows: https://github.com/adam2go/rowtrail

## English thread

1. Agents explore incrementally. Useful work should not disappear with the chat:
   which input version, which SQL, which result, which assumptions, and why?
   RowTrail is a small native workspace for keeping that trail.

2. Already use DuckDB, Polars or Pandas? Keep your cleaning code. Export Parquet,
   snapshot it into RowTrail, and let an agent continue from an independent
   version. Export its results back to your existing plotting/modeling tools.

3. Save a SQL assertion whose rows are violations. Compare two fixed results by
   key. Duplicate keys, partial coverage and execution failures stay explicit.
   Record the input mappings and steps as a recipe; every run gets its own record.

4. Share a branch as a directory: Markdown report, bounded previews, versioned
   manifest, SQL/parameters/provenance and Parquet results. A recipient verifies
   included bytes, imports independent copies and asks another question.
   Import never executes recorded SQL; recomputation is explicit.

5. Native snapshot is shared by CLI/NDJSON/MCP. Higher-level composition starts
   in the optional stdlib Python client. No dataframe engine, model SDK, scheduler
   or provider account was added. Native size ceilings remain 30 MB / 4.5 MB / 125 MB.

6. The limits are public: multiple diff scans, per-job rather than cumulative
   engine budgets, no whole-run transaction or automatic resume. Checksums are
   integrity checks, not signed attestations. Direct engines still win many bare
   SQL tasks. Backend timings are not proof of agent-level token or latency gains.

7. We'd love real workflows, correctness edge cases, integration feedback and
   performance contributions. Can an agent hand useful work to your next session
   or teammate with fewer manual steps? Help us find out.

## 中文说明

RowTrail beta.2 的方向：让 Agent 的分析留下可保存、复用、交接的产物。
你仍然可以用现有工具清洗数据，把 Parquet 交给 RowTrail；独立快照固定输入，检查保存
业务假设，比较解释版本差异，配方负责下次重跑，带报告的分析包让协作者接着提问。

原生 CLI + MCP 与可选 Python 标准库接口各自的范围、安装体积、测试和性能代价都会公开。
我们还需要真实使用证明这些能力有帮助。欢迎带一个工作流、一个失败案例或一份 PR 来。
