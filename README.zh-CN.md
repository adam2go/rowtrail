<div align="center">
  <img src="docs/brand/trail.png" width="720" alt="RowTrail 吉祥物小迹：三行薄荷绿色数据组成的小生物，沿着橙色足迹向前走。">
  <h1>RowTrail</h1>
  <p><strong>探索数据，留下可继续的足迹。</strong></p>
  <p>为 Agent 而生的小型原生数据工具。<br>提出问题，保存精确结果，沿着结果继续探索。</p>
  <p>
    <a href="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml"><img src="https://github.com/adam2go/rowtrail/actions/workflows/ci.yml/badge.svg" alt="构建与验证"></a>
    <a href="https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.8"><img src="https://img.shields.io/badge/release-v0.1.0--alpha.8-147D70" alt="v0.1.0-alpha.8 版本"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-147D70" alt="Apache-2.0 许可证"></a>
  </p>
  <p><a href="README.md">English</a> · <a href="#安装">安装</a> · <a href="docs/agent-guide.md">Agent 接入</a> · <a href="docs/verification.md">测试报告</a> · <a href="CONTRIBUTING.md">参与贡献</a></p>
</div>

---

Agent 探索一张大表，不应该先把整张表塞进上下文。RowTrail 打开本地 CSV/TSV/Parquet，
执行只读 SQL，把带固定版本的结果留在磁盘。Agent 按预算读取带类型的观察，下一次追问
可以直接从保存的结果继续。

**alpha.8 聚焦更快地保存大结果、按资源预算选择 SQL 并行度，以及可按标签找回的中间结果。**
不新增外部依赖，压缩安装包预算继续保持 **30 MB**。

**内部零模型调用，无需 API Key，没有表格界面。** 问什么、证据够不够，由你的 Agent 判断。

| 设计目标 | Agent 实际得到什么 |
|---|---|
| **如实呈现进度** | 明确行组覆盖范围的精确聚合，每个检查点都能固定版本。 |
| **快速理解陌生数据** | 按需统计空值、最小/最大值、常见值；扫描受预算约束。 |
| **减少重复解析 CSV** | 显式流式整理为不可变 Parquet 数据集。 |
| **管理工作区容量** | pin/release、依赖保护 GC，以及托管数据配额。 |
| **探索可以接续** | 通过 `workspace summary` 按标签、行数和字段提示找回固定引用，再用 SQL 复用中间结果。 |
| **节省上下文** | 行数与字节预算、分页观察、精确保留整数与 Decimal。 |
| **任务跨调用存续** | 持久化受理任务，显式等待、事件和取消。 |
| **保持原生与轻量** | 两个可执行文件，运行不需要 Python、Node、Docker 或外部数据库。 |

```text
CSV / TSV / Parquet → 精确查询 → 保存结果 → 继续追问
                         ↓          ↓
                      Agent 按预算读取观察
```

## 安装

当前 **v0.1.0-alpha.8** 是工程预览版，采用 Apache-2.0 许可证。
原生包支持 **macOS arm64** 和 **Linux x86_64**（Ubuntu 22.04 / glibc 2.35 及以上）。

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.8/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

安装器校验 SHA-256，默认安装版本化的二进制组合到 `~/.local/bin`。
可设置 `ROWTRAIL_INSTALL_DIR` 更换目录，也可以从
[Releases](https://github.com/adam2go/rowtrail/releases/tag/v0.1.0-alpha.8) 下载并解压。
请将 `rowtrail` 和 `rowtrail-runtime` 放在同一个目录。

压缩包上限继续为 **30 MB**，CLI / 运行时的解压上限为 **4.5 / 125 MB**。
准确发布大小、校验和与原生验证记录见[验证报告](docs/verification.md#native-distribution)。

**工作区升级：** alpha.8 将元数据升级到 schema 7，旧运行时不能打开升级后的工作区；
如果需要回退，请停止协调器后保留完整的升级前副本。

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

## 不用下载数据的完整演示

克隆仓库后，或在解压的原生包目录中运行：

```sh
python3 examples/quickstart.py --rowtrail /absolute/path/to/rowtrail
```

这个可选的标准库示例生成 **20,003 行 CSV**，发现字段、按有界聚合选择一个地区、保存
退款子集而不返回全部明细，然后重新连接。一次标签目录调用即可找回固定结果，下一次
查询读取 **零原始数据字节**。Python 整数运算独立校验答案，包括大于 2^53 的 ID。
工作区和结果会保留在打印的路径，方便继续探索。[演示源码](examples/quickstart.py)。

## 接入 Agent

| 入口 | 适合的场景 | 开始使用 |
|---|---|---|
| CLI | 能力发现与单次调用 | `rowtrail guide` · `rowtrail schema query` · `rowtrail doctor` |
| 持久 NDJSON 会话 | 同一进程内连续组合操作 | `rowtrail session` · [协议说明](docs/agent-guide.md) |
| MCP stdio | Agent 工具宿主 | `rowtrail --workspace /absolute/private/workspace mcp-config` |
| Rust 客户端 | 原生程序组合 | [`Client::session()` 示例](crates/client/examples/query.rs) |

所有入口共用请求协议、任务和结果存储。断开连接不会取消已受理任务；通信中断不会偷偷
重放可能已经受理的创建操作。可选的 [Python 桥接示例](examples/session_client.py) 只使用
标准库，Python 不是产品运行依赖。使用 `rowtrail python-client > rowtrail_client.py`
即可在本地取得源码；`open` / `query` 响应可以直接作为后续绑定，机械等待无需再经过模型。
[精简接入说明](docs/agent-quickstart.md)。

## 性能有数字，也有依据

alpha.8 优化的是完整探索：打开陌生数据、保存子集、从结果分支，再回原始数据追问。
测试明确记录并行度，对照引擎允许保留中间表。
[验证报告](docs/verification.md) 保留重复原始计时、独立正确性检查、原生产物哈希与负面实验。

SQLite FULL 提交、文件与目录同步、内容校验和真实取消仍然保留。较大的压缩结果文件
能减少持久化提交次数，但从大结果读取很小的分页可能变慢；内存池预算也不是 RSS 上限。
安装包体积预算不变。

**直接引擎仍然更快。** RowTrail 还承担持久任务、固定结果、校验、进程隔离和有界协议的
成本。如果已有持久 DuckDB 环境，并能自行管理这些生命周期，它仍是很强的选择。
最新真实 Agent 配对试验仍为 alpha.6：12 次全部答对，但 RowTrail 耗时更长、累计输入
token 更多。本轮后端优化和接续演示不能证明模型整体耗时或 token 优势。

[当前证据](docs/verification.md) · [alpha.8 原始记录](benchmarks/performance/alpha8/) ·
[alpha.7 历史报告](docs/releases/alpha7-verification.md)。

## 接下来的方向

渐进聚合支持完整文件和行组，不支持 GROUP BY 和过滤。抽样估计、任务中断后续算、
SQL 预备计划缓存、原生 MCP Tasks、宿主自动接续/淘汰和远程来源仍未实现。[当前能力与限制](docs/progress.md) 会与规划分开记录。

我们希望它一直容易安装、容易组合、容易理解。欢迎让协议更清楚、安装包更小、完整探索
更快的贡献。从 [贡献指南](CONTRIBUTING.md)、[源码构建](docs/usage.md#build) 或一个
[可复现的问题](https://github.com/adam2go/rowtrail/issues) 开始。

<sub>认识 <a href="docs/brand/README.md">Trail / 小迹</a>，我们的三行数据伙伴。一个问题，一个结果，再向前一步。</sub>
