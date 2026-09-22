# Introducing RowTrail alpha.8

Copy is for the verified alpha.8 engineering preview. Link the release and
[measurement report](verification.md); the user publishes social posts themselves.

## Short announcement

RowTrail alpha.8: a small native data workspace for agents.

Explore CSV/Parquet, save exact results, and pick up where you left off without
putting whole tables in the prompt.

CLI + MCP. Zero internal model calls. Apache-2.0.

https://github.com/adam2go/rowtrail

## English thread

1. An agent rarely knows its full analysis plan when it first sees a table.
   It needs to inspect, ask a question, change direction and keep useful results.
   We built RowTrail around that loop: Explore data. Keep the trail.

2. RowTrail opens local CSV/TSV/Parquet, runs read-only SQL and keeps immutable
   result revisions. The agent receives bounded, typed observations. It can query
   a saved subset directly, without sending all its rows through the model.

3. Alpha.8 improves large-result persistence and uses resource-aware parallelism.
   Labels and bounded catalog hints let a fresh connection find the right saved
   result. A runnable example demonstrates handoff in one catalog call + one query.

4. On one Mac, 1M-row exploration: 231 → 142 ms (7 alternating trials).
   A 32 MiB sort: 866 → 437 ms (5 trials). Controls match maximum query targets.
   Direct engines remain faster; tiny pages from big results regress.

5. It is an Apache-2.0 engineering preview: small native CLI + runtime, MCP and
   persistent sessions, zero internal model calls. Native packages support macOS
   arm64 and Linux x86_64. No Python, Node, Docker or model API key is required.

6. Help us test it on real agent tasks: a reproducible slow workflow, a confusing
   contract, a smaller binary, or an integration that takes less setup.
   Independent comparisons and negative results are welcome.
   https://github.com/adam2go/rowtrail

## 中文介绍

Agent 拿到一张陌生表时，通常并不知道完整的分析路径。它需要先观察，提出一个问题，
根据结果改变方向，再继续追问。我们希望探索过程中的结果能留在工具里，而不是不断
复制到模型上下文中。这是 RowTrail 的出发点：**探索数据，留下路径。**

RowTrail 是一个专门给 Agent 使用的本地数据工具。它打开 CSV、TSV、Parquet，执行只读
SQL，将结果保存为不可变版本，按行数和字节预算返回带类型的观察。下一步可以直接绑定
已存结果；任务受理、等待、取消、结果质量和截断状态都有明确的协议。

alpha.8 继续围绕 **Agent 原生、小而美、性能强** 打磨：根据资源预算选择查询并行度，
减少保存可压缩结果时的持久化提交，增加结果标签、行数和字段提示，让重连后的 Agent
更容易找回之前的工作。原生包还带了一份无需下载数据的完整演示，答案会用独立整数
运算校验。具体性能数字、机器条件、原生检查和负面结果全部放在验证报告中。

它还不是成熟数据库的替代品。直接使用持久 DuckDB 仍然很强；RowTrail 额外承担持久任务、
固定结果、校验与隔离的成本。较大的结果文件会拖慢很小的分页，渐进聚合目前不支持
GROUP BY 或过滤，也没有中断续算、远程数据源或 Windows 包。我们还没有证明真实 Agent
整体耗时和 token 用量优于持久 DuckDB。

前期我们更关心项目是否有用、是否容易理解和传播。Apache-2.0 开源，无内部模型调用，
不绑定模型厂商。欢迎一起贡献真实任务、可复现的性能问题、接入示例、体积优化和更清晰
的协议。尤其欢迎独立测量，以及指出我们没有做好的地方。

项目：https://github.com/adam2go/rowtrail

## A short recording

1. Open the README at “Explore data. Keep the trail.” State the problem: an
   agent's analysis changes direction; whole tables should not occupy its prompt.
2. Show `rowtrail --version`. From the repository or extracted archive, run:

   ```sh
   python3 examples/quickstart.py
   ```

3. Point to 20,003 input rows, the four region totals and the selected region.
   The saved subset has 385 rows; only its three channel totals are shown.
4. Show the handoff: one catalog call, one query, zero original-source bytes.
   The retained ID `9007199254740993` remains an exact string, not a rounded float.
5. Open the verification report. Explain one performance gain and the small-page
   tradeoff, then finish on CONTRIBUTING.md. Do not present demo timing as an
   independent model-efficiency benchmark.

## Questions to expect

**Why not simply use persistent DuckDB?** You can. RowTrail packages durable job
lifecycle, fixed result bindings, explicit quality and bounded observation behind
CLI/session/MCP interfaces. DuckDB remains faster in our direct-engine controls.
Use the simplest tool that serves the task.

**Is “exact” arbitrary precision?** No. Wire Int64/UInt64/Decimal values retain
their types and digits, but SQL operates on finite-width types. The pinned engine
can wrap an overflowing Int64 SUM. Cast wide integer sums to `DECIMAL(38,0)` (or
the needed scale) and stay within its precision. Use an explicit Decimal schema
for money. “Exact” also does not turn a partial source prefix into a population
answer; quality, completion and truncation remain separate.

**Can the original file change after saving?** External file identities are
checked when bindings are used. Changed or missing sources can invalidate derived
results; zero source-data bytes does not mean no identity check. Explicit CSV/TSV
preparation creates an independent managed Parquet copy for repeated exploration.

**Does it resume an interrupted calculation?** No. Accepted jobs survive a client
disconnect. Worker/coordinator failure can leave readable committed revisions,
with explicit partial coverage. Interrupted computation is not automatically
continued or replayed. Labels do not change this contract.

**What does the memory limit cover?** The query engine's MemoryPool, not total
RSS. Codec buffers, metadata, verified-part caches and other process allocations
are separate. Reports include sampled RSS and real spilling; it is not a hard cap.

**What is ready to contribute?** Real agent workflows with independent correctness
oracles; bounded client examples; projected reads with less integrity-I/O overhead;
smaller native artifacts; more native targets. Discuss a substantial new engine
or workflow first so the project stays small and agent-focused.
