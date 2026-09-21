<div align="center">
  <img src="docs/brand/trail.png" width="720" alt="RowTrail 吉祥物小迹：三行薄荷绿色数据组成的小生物，沿着橙色足迹向前走。">
  <h1>RowTrail</h1>
  <p><strong>探索数据，留下可继续的足迹。</strong></p>
  <p>为 Agent 而生的小型原生数据工具。<br>提出问题，保存精确结果，沿着结果继续探索。</p>
  <p>
    <a href="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml"><img src="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml/badge.svg" alt="构建与验证"></a>
    <a href="https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.6"><img src="https://img.shields.io/badge/release-v0.1.0--alpha.6-147D70" alt="v0.1.0-alpha.6 版本"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-147D70" alt="Apache-2.0 许可证"></a>
  </p>
  <p><a href="README.md">English</a> · <a href="#安装">安装</a> · <a href="docs/agent-guide.md">Agent 接入</a> · <a href="docs/verification.md">测试报告</a> · <a href="CONTRIBUTING.md">参与贡献</a></p>
</div>

---

Agent 探索一张大表，不应该先把整张表塞进上下文。RowTrail 打开本地 CSV/TSV/Parquet，
执行只读 SQL，把带固定版本的结果留在磁盘。Agent 按预算读取带类型的观察，下一次追问
可以直接从保存的结果继续。

**alpha.6 聚焦性能：压缩中间结果、减少持久化提交成本、加快分页，并缩短 Agent 接入上下文。**
本机交替对照中，百万行完整探索约快 14%，一万行分页约快 60%；安装包继续受 30 MB 上限约束。

**内部零模型调用，无需 API Key，没有表格界面。** 问什么、证据够不够，由你的 Agent 判断。

| 设计目标 | Agent 实际得到什么 |
|---|---|
| **如实呈现进度** | 明确行组覆盖范围的精确聚合，每个检查点都能固定版本。 |
| **快速理解陌生数据** | 按需统计空值、最小/最大值、常见值；扫描受预算约束。 |
| **减少重复解析 CSV** | 显式流式整理为不可变 Parquet 数据集。 |
| **管理工作区容量** | pin/release、依赖保护 GC，以及托管数据配额。 |
| **探索可以接续** | 通过 `workspace summary` 找回固定引用，再用 SQL 复用中间结果。 |
| **节省上下文** | 行数与字节预算、分页观察、精确保留整数与 Decimal。 |
| **任务跨调用存续** | 持久化受理任务，显式等待、事件和取消。 |
| **保持原生与轻量** | 两个可执行文件，运行不需要 Python、Node、Docker 或外部数据库。 |

```text
CSV / TSV / Parquet → 精确查询 → 保存结果 → 继续追问
                         ↓          ↓
                      Agent 按预算读取观察
```

## 安装

当前 **v0.1.0-alpha.6** 是工程预览版，采用 Apache-2.0 许可证。
原生包支持 **macOS arm64** 和 **Linux x86_64**（Ubuntu 24.04 / glibc 2.39 及以上）。

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.6/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

安装器校验 SHA-256，默认安装版本化的二进制组合到 `~/.local/bin`。
可设置 `ROWTRAIL_INSTALL_DIR` 更换目录，也可以从
[Releases](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.6) 下载并解压。
请将 `rowtrail` 和 `rowtrail-runtime` 放在同一个目录。

alpha.6 候选版的原生产物验证正在进行。本地 macOS 构建压缩包约 19.19 MB；
最终 Linux/macOS 大小将在 CI 产物校验后记录。下载包上限 **30 MB**，CLI / 运行时上限 4.5 / 125 MB。
[原生产物验证](docs/verification.md#native-distribution)。

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
[渐进示例](examples/progressive.py) 则演示观察检查点，并在覆盖足够多行组或文件后显式取消。

## 接入 Agent

| 入口 | 适合的场景 | 开始使用 |
|---|---|---|
| CLI | 能力发现与单次调用 | `rowtrail guide` · `rowtrail schema query` · `rowtrail doctor` |
| 持久 NDJSON 会话 | 同一进程内连续组合操作 | `rowtrail session` · [协议说明](docs/agent-guide.md) |
| MCP stdio | Agent 工具宿主 | `rowtrail --workspace /absolute/private/workspace mcp-config` |
| Rust 客户端 | 原生程序组合 | [`Client::session()` 示例](crates/client/examples/query.rs) |

所有入口共用请求协议、任务和结果存储。断开连接不会取消已受理任务；通信中断不会偷偷
重放可能已经受理的创建操作。可选的 [Python 桥接示例](examples/session_client.py) 只使用
标准库，Python 不是产品运行依赖。

## 性能有数字，也有依据

同一台 Apple arm64 Mac，**七轮交替运行新旧版本，取中位数**，单位毫秒。
完整五步探索包含打开输入、物化结果、两次结果分支，再回到原始数据查询其他维度。

| 调用方式 | 16,384 行 | 1,048,576 行 |
|---|---:|---:|
| RowTrail alpha.5 · 本次重跑 | 109.46 | 348.38 |
| **RowTrail alpha.6 · 持久 NDJSON** | **110.34** | **298.90** |
| DuckDB 1.5.5 · 持久会话 | 10.62 | 92.19 |
| 直接 DataFusion 55.0.0 · 持久会话 | 4.76 | 59.05 |

百万行任务约快 **14%**，小数据基本持平。直接引擎仍然更快；RowTrail 还承担持久化、
内容校验、进程隔离和协议成本。对照保留内存中间表，DataFusion 计时不含启动。
这些是本机测量，不代表普遍性能优势。

- **少读磁盘：** 百万行样例的保存结果由 33.52 MB 降到 **4.22 MB**，约减少 87%。
  分片从 9 个减为 6 个；后续分支仍校验保存文件，原始数据读取为零。
- **更快分页：** 10,000 行固定结果读取从 **21.92 ms 降到 8.80 ms**，均含 CLI 启动，五次中位数。
- **检查难压缩数据：** 高熵整数的保存加三次复用，七轮交替中位数从 150.03 ms 降到 **138.75 ms**。
- **保持资源约束：** 百万行排序在 32 MiB 引擎池下实际溢写 26 次，逐一核对全部导出 ID；
  本次采样 worker RSS 约 139.7 MB。引擎池预算不是进程 RSS 上限。

渐进观察仍然有效：单文件 16 个行组，首个前缀 **19.40 ms**，完整完成 **45.32 ms**，
与 alpha.5 本次重跑基本持平。只要最终答案时，普通 SQL 仍更快。
[原始记录和完整条件](docs/verification.md)。

**Agent 效率也要实测。** 新增[精简启动说明](docs/agent-quickstart.md)、按需 schema、机械等待和
保留完整观察信息的精简展示 helper。更新后的真实 Agent 配对试验正在运行；
[alpha.5 的 12 次历史试验](docs/releases/alpha5-verification.md#real-external-agent-paired-pilot)全部答对，
但当时 RowTrail 比持久 DuckDB 更慢、累计输入 token 更多。引擎改进不等于已经证明 Agent 整体效率领先。

本地已有 **73 项集成场景**、7 项 Rust 测试（含八种提交边界子进程崩溃场景），
以及 MCP、会话、SDK、安装和新旧版本互读验证。压缩文件仍能识别同大小、同修改时间的篡改。

[完整验证报告](docs/verification.md) · [alpha.6 原始记录](benchmarks/performance/alpha6/) ·
[alpha.5 历史报告](docs/releases/alpha5-verification.md)

## 接下来的方向

渐进聚合支持完整文件和行组，不支持 GROUP BY 和过滤。抽样估计、任务中断后续算、
SQL 预备计划缓存、原生 MCP Tasks、宿主自动接续/淘汰和远程来源仍未实现。[当前能力与限制](docs/progress.md) 会与规划分开记录。

我们希望它一直容易安装、容易组合、容易理解。欢迎让协议更清楚、安装包更小、完整探索
更快的贡献。从 [贡献指南](CONTRIBUTING.md)、[源码构建](docs/usage.md#build) 或一个
[可复现的问题](https://github.com/adam2go/rowtrail/issues) 开始。

<sub>认识 <a href="docs/brand/README.md">Trail / 小迹</a>，我们的三行数据伙伴。一个问题，一个结果，再向前一步。</sub>
