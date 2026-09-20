<div align="center">
  <img src="docs/brand/trail.png" width="720" alt="RowTrail 吉祥物小迹：三行薄荷绿色数据组成的小生物，沿着橙色足迹向前走。">
  <h1>RowTrail</h1>
  <p><strong>探索数据，留下可继续的足迹。</strong></p>
  <p>为 Agent 而生的小型原生数据工具。<br>提出问题，保存精确结果，沿着结果继续探索。</p>
  <p>
    <a href="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml"><img src="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml/badge.svg" alt="构建与验证"></a>
    <a href="https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.3"><img src="https://img.shields.io/badge/release-v0.1.0--alpha.3-147D70" alt="v0.1.0-alpha.3 版本"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-147D70" alt="Apache-2.0 许可证"></a>
  </p>
  <p><a href="README.md">English</a> · <a href="#安装">安装</a> · <a href="docs/agent-guide.md">Agent 接入</a> · <a href="docs/verification.md">测试报告</a> · <a href="CONTRIBUTING.md">参与贡献</a></p>
</div>

---

Agent 探索一张大表，不应该先把整张表塞进上下文。RowTrail 打开本地 CSV/TSV/Parquet，
执行只读 SQL，把带固定版本的结果留在磁盘。Agent 按预算读取带类型的观察，下一次追问
可以直接从保存的结果继续。

**alpha.3 新增：按需画像、显式 CSV/TSV → Parquet 整理、工作区容量与回收。**
保存结果的继续查询和导出也会校验内容完整性。

**内部零模型调用，无需 API Key，没有表格界面。** 问什么、证据够不够，由你的 Agent 判断。

| 设计目标 | Agent 实际得到什么 |
|---|---|
| **快速理解陌生数据** | 按需统计空值、最小/最大值、常见值；扫描受预算约束。 |
| **减少重复解析 CSV** | 显式流式整理为不可变 Parquet 数据集。 |
| **管理工作区容量** | pin/release、依赖保护 GC，以及托管数据配额。 |
| **探索可以接续** | 固定数据清单和结果版本，通过 SQL 复用中间结果。 |
| **节省上下文** | 行数与字节预算、分页观察、精确保留整数与 Decimal。 |
| **任务跨调用存续** | 持久化受理任务，显式等待、事件和取消。 |
| **保持原生与轻量** | 两个可执行文件，运行不需要 Python、Node、Docker 或外部数据库。 |

```text
CSV / TSV / Parquet → 精确查询 → 保存结果 → 继续追问
                         ↓          ↓
                      Agent 按预算读取观察
```

## 安装

当前 **v0.1.0-alpha.3** 是工程预览版，采用 Apache-2.0 许可证。
原生包支持 **macOS arm64** 和 **Linux x86_64**（Ubuntu 24.04 / glibc 2.39 及以上）。

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.3/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

安装器校验 SHA-256，默认安装版本化的二进制组合到 `~/.local/bin`。
可设置 `ROWTRAIL_INSTALL_DIR` 更换目录，也可以从
[Releases](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.3) 下载并解压。
请将 `rowtrail` 和 `rowtrail-runtime` 放在同一个目录。

| 平台 | `.tar.xz` 下载包 | 安装后两个可执行文件 |
|---|---:|---:|
| macOS arm64 | 19.03 MB | 103.40 MB |
| Linux x86_64 | 22.31 MB | 118.49 MB |

两个平台的原生 CI 均已通过。MB 使用十进制；安装体积不含许可说明与工作区数据。
压缩包 30 MB 的上限继续由 CI 检查。
[精确大小、校验和与构建来源 →](docs/verification.md#native-distribution)

## 先问一个问题

安装后直接运行，不用另外下载数据：

```sh
rowtrail query --sql "SELECT region, SUM(amount) AS total
  FROM (VALUES ('east', 12), ('east', 8), ('west', 7)) AS orders(region, amount)
  GROUP BY region ORDER BY region"
```

精确结果是 `east = 20`、`west = 7`。返回 JSON 包含稳定的任务引用；如果在等待预算内
已有可读结果，还会包含有界的结果观察。`ok: true` 表示调用被接受，是否计算完成要看 `job.state`。

处理自己的文件，从 `rowtrail open ./orders.parquet` 开始。使用返回的 Dataset 和 Manifest ID
绑定查询，再把固定结果版本交给下一次查询。
[完整操作指南](docs/usage.md#try-the-exact-exploration-loop) 包含等待、分页、分支探索与导出；
[可运行的组合示例](examples/explore.py) 会自动传递这些引用。
[alpha.3 完整示例](examples/prepare_explore.py) 还串起了画像、整理、两次分支、导出和回收。

## 接入 Agent

| 入口 | 适合的场景 | 开始使用 |
|---|---|---|
| CLI | 能力发现与单次调用 | `rowtrail schema query` · `rowtrail doctor` |
| 持久 NDJSON 会话 | 同一进程内连续组合操作 | `rowtrail session` · [协议说明](docs/agent-guide.md) |
| MCP stdio | Agent 工具宿主 | `rowtrail --workspace /absolute/private/workspace mcp` |
| Rust 客户端 | 原生程序组合 | [`Client::session()` 示例](crates/client/examples/query.rs) |

所有入口共用请求协议、任务和结果存储。断开连接不会取消已受理任务；通信中断不会偷偷
重放可能已经受理的创建操作。可选的 [Python 桥接示例](examples/session_client.py) 只使用
标准库，Python 不是产品运行依赖。

## 性能有数字，也有依据

同一台 Apple arm64 Mac，**每组运行五次，取中位数**，单位毫秒，越低越好。
完整的五次查询探索包含打开输入、物化结果、两次结果分支，再回到原始数据查询其他维度。

| 调用方式 | 16,384 行 | 1,048,576 行 |
|---|---:|---:|
| RowTrail alpha.1 · CLI | 349.58 | 12,917.72 |
| RowTrail alpha.2 · CLI | 123.20 | 377.35 |
| **RowTrail alpha.3 · CLI** | **122.92** | **345.68** |
| RowTrail alpha.2 · 持久 NDJSON | 104.13 | 360.55 |
| **RowTrail alpha.3 · 持久 NDJSON** | **103.99** | **322.28** |
| DuckDB 1.5.5 · 持久会话 | 9.39 | 92.23 |
| 直接 DataFusion 55.0.0 · 持久会话 | 4.56 | 58.93 |

alpha.3 已包含结果复用时的内容校验。在这台机器的百万行探索中，相对 alpha.2，
CLI 快约 **8.4%**、持久会话快约 **10.6%**。直接引擎在此测试中仍然更快；RowTrail 还承担
持久化、进程隔离和协议成本。对照保留内存中间表，DataFusion 计时不含启动。
本机测量不代表普遍性能优势，也不是 Agent 采用效果实验。

**整理有前置成本。** 另一组百万行 CSV 测试中，转换约需 271 ms。打开、画像并做十次
后续聚合，直接读 CSV 共 736 ms，计入整理后共 652 ms。这组数据到第八次追问才回本；
具体阈值会随数据和问题变化，因此 RowTrail 把是否整理的选择留给 Agent。

alpha.3 在原有 30 项集成检查上增加了 **13 组场景**，并对 Arrow 结果和 Parquet 整理
分别注入四个提交边界的进程崩溃。6 项 Rust 测试包含该子进程测试框架。
**1,048,576 行排序**在 **32 MiB 引擎内存池**下实际落盘，所有导出 ID 与独立排序逐项一致。
内存池上限不等于整个进程的内存上限。

[验证报告与测试清单](docs/verification.md) ·
[测量方法与资源测试](benchmarks/README.md) ·
[alpha.3 原始性能记录](benchmarks/performance/alpha3/)

## 接下来的方向

下一步是受限的 Parquet 渐进聚合。抽样估计、SQL 预备计划缓存、原生 MCP Tasks、宿主自动接续、
自动淘汰策略和远程来源尚未实现。[当前能力与限制](docs/progress.md) 会与规划分开记录。

我们希望它一直容易安装、容易组合、容易理解。欢迎让协议更清楚、安装包更小、完整探索
更快的贡献。从 [贡献指南](CONTRIBUTING.md)、[源码构建](docs/usage.md#build) 或一个
[可复现的问题](https://github.com/adam2go/rowtrail/issues) 开始。

<sub>认识 <a href="docs/brand/README.md">Trail / 小迹</a>，我们的三行数据伙伴。一个问题，一个结果，再向前一步。</sub>
