# Beta.3 launch copy and runnable demo

These are drafts for the maintainer. Nothing is posted automatically.

## Short English post

RowTrail beta.3: spend context on evidence.

One command generates a 100K-row demo, saves the analysis, runs checks and hands
it to another process to continue—even after the original CSV is gone.

Compact responses keep full quality. In our scripted test: 27.7% fewer response
tokens. Raw transcripts, faster DuckDB controls and limitations are public.

Agent-native. Small native install. Apache-2.0. Zero internal model calls.

https://github.com/adam2go/rowtrail

## English thread

1. An agent shouldn't need the whole table—or the whole job log—to answer its
next question. RowTrail beta.3 adds compact evidence, column-name discovery and
saved-analysis context. `rowtrail demo` runs the complete workflow offline.

2. The demo creates 100,000 orders and 64 columns. It saves 77,922 qualifying
orders without showing those rows, returns four channel totals, checks unique
IDs and reconnects using a label. Purpose, SQL and fixed input versions remain.

3. A separate recipient imports the portable report/results after the original
CSV is deleted. It explicitly runs a recipe to recheck IDs and reproduce the
aggregate from the included intermediate. Missing raw inputs remain a declared
limit. Imported SQL never executes automatically.

4. Seven scripted trials: native response tokens 4,753 → 3,438 with o200k_base.
Including request tokens, the sum of medians falls 22.2%. These are tokenizer
counts—not a model bill or evidence that every agent task improves.

5. Direct DuckDB is still faster and sends less metadata. It can persist tables
and return small answers too. RowTrail adds common contracts for fixed versions,
quality, purpose, checks and handoff. We publish the extra cost, not just wins.

6. We'd love contributions from people using agents on real datasets: try the
demo, bring an existing DuckDB/Polars/Pandas workflow, report friction, or help
make handoffs clearer and the package smaller. Apache-2.0, no internal model calls.

## 中文长文提纲

RowTrail 的目标是让 Agent 低成本地探索陌生数据，并留下可以继续使用、验证和交接的分析。
这版聚焦实际调用时的几个负担：宽表列太多、工具响应夹带运行日志、大结果挤占上下文，
以及换一个 Agent 后不知道之前结果的用途与来源。

beta.3 用精简响应、按列名发现、一次调用恢复上下文、有界等待结果和可执行 demo 来解决。
质量、数值语义、错误与截断不会被简化成一个“成功”标签。原始表留在工具里，模型只看必要证据。

七轮固定任务中，响应 token 减少 27.7%；包含请求的中位数之和减少 22.2%。这是明确
编码下的脚本分词结果，不是真实模型账单。已有大工作流小幅变慢，直接 DuckDB 仍然更快，
这些都在原始记录里。我们希望凭借完整工作流增加价值，而不是声称数据库无法做这些事。

欢迎真实数据、Agent 接入经验、性能实验、协议设计和代码贡献。我们仍要验证这些能力
是否能让不同 Agent 在实际任务中更高效。项目坚持 Agent 原生、小而美、性能强。

## Demonstration sequence

```sh
rowtrail demo --directory ./rowtrail-demo
```

Show `result.json` (four rows and verification scope), then `report.md`, the
portable `handoff/report.md`, and `receiver-run/run.json`. Finish by using the
printed client against the retained workspace with `find` and one bounded query.
Do not present script timing as model reasoning latency or claim full original
reproduction when only the saved intermediate is included.
