# Introducing RowTrail beta.1

Copy for the beta.1 release. The user publishes social posts themselves. Link
[the release](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-beta.1) and
[measurement report](verification.md); retain the conditions with timing claims.

## Short announcement

RowTrail enters beta: a small native data workspace built for agents.

Explore CSV/Parquet, save a result, keep asking. Bounded observations, checked
integer/Decimal SUM, stable reconnection. CLI + MCP. Zero internal model calls.

Apache-2.0. Help us test real workflows:
https://github.com/adam2go/rowtrail

## English thread

1. An agent rarely knows its full analysis plan when it first sees a table.
   It needs to inspect, ask a question, change direction and keep useful results.
   We built RowTrail around that loop: Explore data. Keep the trail.

2. RowTrail opens local CSV/TSV/Parquet, runs read-only SQL and keeps immutable
   result revisions. The agent receives bounded, typed observations. It can query
   a saved subset directly, without sending all its rows through the model.

3. Beta.1 follows independent feedback: checked integer/Decimal SUM, invalid
   Decimal rejection, stable workspace reconnection and a stdlib client with
   strict labels, prepare/inspect/export and explicitly budgeted pagination.

4. On one Mac, seven alternating trials: save a large subset from 2M rows,
   ask ten more questions, reconnect and recover it. Complete time: 934 → 720 ms.
   Follow-up saved-data reads: 82 → 28 MB each, zero original-data bytes.
   Independent integer/Decimal answers; no model calls in this benchmark.

5. Costs stay visible: save time +4%, separate 1M-row exploration +4%, response
   bytes +9%. Warm-query p95 rises. Direct engines remain faster. We have not
   established a real-agent time or token advantage over persistent DuckDB.
   Raw samples, rejected candidates and correctness tests are all in the repo.

6. Agent-native. Small native CLI + runtime. Apache-2.0. No internal model calls,
   provider account, Python/Node/Docker runtime dependency or spreadsheet UI.
   macOS arm64 and Linux x86_64. Beta means a tested local workflow, not 1.0
   metadata stability or arbitrary-precision SQL.

7. Help us make it useful: real agent tasks, confusing contracts, slow workflows,
   smaller binaries, or integrations that take less setup. Independent comparisons
   and negative results are welcome.
   https://github.com/adam2go/rowtrail

## 中文介绍

Agent 拿到一张陌生表时，通常并不知道完整的分析路径。它需要先观察，提出一个问题，
根据结果改变方向，再继续追问。我们希望探索过程中的结果能留在工具里，而不是不断
复制到模型上下文中。这是 RowTrail 的出发点：**探索数据，留下路径。**

RowTrail 是专门给 Agent 使用的本地数据工具。它打开 CSV、TSV、Parquet，执行只读
SQL，将结果保存为不可变版本，按行数和字节预算返回带类型的观察。下一步可以直接绑定
保存结果；任务受理、等待、取消、结果质量和截断状态都有明确协议。

beta.1 根据独立测试反馈补齐四个方向：整数/Decimal SUM 溢出检查，阻止无效 Decimal
被错误显示；跨 TMPDIR 的稳定重连；轻量客户端的整理、画像、导出、严格标签查找和有界
分页；保存大结果后的连续复用。数值保证明确限定范围，旧结果不会被悄悄重新认证。

性能方面，在一台 Mac 上做了 7 轮交替测试：从 2M 行来源保存子集，连续追问十次，再
重连找回结果。完整任务从 934 ms 降到 720 ms；十次追问从 567 ms 降到 375 ms，每次
保存数据读取从 82 MB 减少到 28 MB，原始数据读取为零。独立整数/Decimal 运算校验答案。
没有新增产品依赖，校验、持久化和真实取消也继续保留。

我们同样公开代价：首次保存约慢 4%，另一组百万行探索约慢 4%，返回字节增加约 9%，
热查询尾部延迟也上升了。直接使用持久 DuckDB 仍然很强；RowTrail 额外承担持久任务、
固定结果、校验与隔离的成本。目前还没有证明真实 Agent 的总耗时或 token 优势。完整
样本、失败候选、性能图和原生验收证据都留在项目中。

**Agent 原生、小而美、性能强** 是我们继续做取舍的依据。Beta 表示本地核心流程已经
可以让更多开发者试用，不等于 1.0 兼容承诺。渐进聚合仍不支持 GROUP BY 或过滤，没有
计算中断续算、远程来源和 Windows 包。浮点、AVG、其他 SQL 运算也不属于本轮 SUM
检查的保证范围。

前期我们更关心项目是否有用、是否容易理解和传播。Apache-2.0 开源，无内部模型调用，
不绑定模型厂商。欢迎贡献真实任务、可复现的性能问题、接入示例、体积优化和更清晰的
协议。尤其欢迎独立测量，以及指出我们没有做好的地方。

项目：https://github.com/adam2go/rowtrail

## A short recording

1. Open the README at “Explore data. Keep the trail.” Explain why an agent's
   changing analysis should retain useful results outside the prompt.
2. Show `rowtrail --version`. From the repository or extracted archive, run:

   ```sh
   python3 examples/quickstart.py
   ```

3. Point to 20,003 input rows, the region totals and the selected region. The
   saved subset has 385 rows; only its three channel totals are shown.
4. Show handoff: one catalog call, one query, zero original-source bytes. The
   retained ID `9007199254740993` remains an exact string, not a rounded float.
5. Open the verification report. Show the complete-task benefit and a regression.
   Finish on CONTRIBUTING.md. Do not turn demo timing into a model-efficiency claim.

## Questions to expect

**Why not simply use persistent DuckDB?** You can. RowTrail packages durable job
lifecycle, fixed result bindings, explicit quality and bounded observation behind
CLI/session/MCP interfaces. DuckDB remains faster in our direct-engine controls.
Use the simplest tool that serves the task.

**Is “exact” arbitrary precision?** No. It describes sampling. Wire integer and
Decimal values retain their digits; beta.1 checks integer/Decimal SUM against the
result's declared range and rejects precision-invalid Decimal output. Float SUM,
AVG, other arithmetic and casts retain engine semantics. An explicit wider
Decimal helps only within its finite range. Old wrapped integers cannot be
recovered; unknown input ancestry remains unknown. See the numeric contract.

**Can the original file change after saving?** External file identities are
checked when bindings are used. Changed or missing sources can invalidate derived
results; zero source-data bytes does not mean no identity check. Explicit CSV/TSV
preparation creates an independent managed Parquet copy for repeated exploration.

**Does it resume an interrupted calculation?** No. Accepted jobs survive a client
disconnect. Worker/coordinator failure can leave readable committed revisions,
with explicit partial coverage. Interrupted computation is not automatically
continued or replayed. Labels do not change this contract.

**What does the memory limit cover?** The query engine's MemoryPool, not total
RSS. Codec buffers, metadata, verified-part caches and other allocations are
separate. Reports include sampled RSS and real spilling; it is not a hard cap.

**What is ready to contribute?** Real agent workflows with independent correctness
oracles; warm-query tail latency; better observation size; projected reads with
less integrity-I/O overhead; smaller artifacts and more native targets. Discuss
a substantial new engine or workflow first so the project stays small and focused.
