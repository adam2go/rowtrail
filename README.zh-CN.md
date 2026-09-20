# RowTrail

[English](README.md) · [下载](https://github.com/adam2go/rowtrail/releases)

面向 Agent 的本地结构化数据探索工具。打开陌生 CSV 或 Parquet，执行只读 SQL，
保存带固定版本的结果，再基于已有结果继续追问。结果留在磁盘，Agent 按预算读取观察。
工具内部不调用模型，分析方向与结论由外部 Agent 判断。

当前是 **0.1.0-alpha.2 工程预览版**，采用 Apache-2.0。已实现第一条精确探索闭环，
尚未实现抽样估计、宿主自动接续、原生 MCP Tasks、自动 GC 和远程来源。
完整范围见 [进度与限制](docs/progress.md)。

本版原生下载包约 **macOS 19 MB / Linux 22 MB**。同机同 CLI 的五步探索，
16K 行约比上一版快 2.8 倍，百万行约快 34 倍；10,000 行分页从 570 ms 降到 21 ms。
这是相对上一版的本机实测，完整条件、原始数据和成熟引擎对照见 [基准说明](benchmarks/README.md)。

## 安装与运行

初期支持 macOS 和 Linux。将下载包内的 `rowtrail` 与 `rowtrail-runtime` 放在同一个
`PATH` 目录即可。运行发布包不需要 Rust、Python、Node、Docker 或外部数据库。

发布包提供 macOS arm64 和 Linux x86_64（Ubuntu 24.04 / glibc 2.39 及以上）版本。
安装器校验 SHA-256，默认安装到 `~/.local/bin`，也可设置 `ROWTRAIL_INSTALL_DIR`：

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.2/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

也可以下载 `.tar.xz` 包后解压。从源码构建需要 Rust 1.94.0 和 C 编译器：

```sh
cargo build --release --locked
export PATH="$PWD/target/release:$PATH"
rowtrail doctor
rowtrail-runtime fixtures --directory /tmp/rowtrail-data --rows 16384
rowtrail open /tmp/rowtrail-data/small.parquet
```

用返回的 Dataset 与 Manifest ID 绑定输入：

```sh
rowtrail query --bind orders=ds_ID@mf_ID \
  --sql 'SELECT region, SUM(amount) AS total FROM orders GROUP BY region'
rowtrail job wait job_ID --wait-ms 1000
rowtrail read res_ID --revision 2 --max-rows 20
rowtrail query --bind saved=res_ID@2 --sql 'SELECT * FROM saved WHERE total > 0'
rowtrail export res_ID --revision 2 --format parquet --output ./result.parquet
```

示例中的 ID 和 revision 要替换成实际返回值。便宜查询直接返回结果观察；耗时查询返回
真实后台任务。关闭提交命令不会停止计算，`rowtrail job cancel job_ID` 才会取消。

如果想直接运行完整示例，无需手动替换 ID：

```sh
python3 examples/explore.py /tmp/rowtrail-data/many.parquet --rowtrail ./target/release/rowtrail
```

这个可选示例只用 Python 标准库，通过一个持久 `rowtrail session` 进程连续调用，自动传递固定结果引用，并输出两个分支的原始数据/结果读取计数。

## 接入 Agent

CLI、MCP 和 Rust SDK 共用请求结构、任务状态与持久结果。

```sh
rowtrail schema query
rowtrail --workspace /absolute/private/workspace mcp
rowtrail events --job job_ID --follow --jsonl
```

MCP 宿主配置中，command 使用 `rowtrail` 的绝对路径，args 使用
`["--workspace", "/absolute/private/workspace", "mcp"]`，通信方式为 stdio。
复杂操作可以用 `rowtrail call METHOD --request request.json` 由程序组合。
高频程序组合优先使用 `rowtrail session`：stdin 每行发送一个完整请求 envelope，stdout
每行返回一个响应，省去逐次启动 CLI 和握手。`Client::session()` 提供相同的 Rust 接口。
关闭会话不取消已受理任务；通信中断不会偷偷重试创建操作。见 [Agent 接入指南](docs/agent-guide.md)。
Rust 接入示例见 [query.rs](crates/client/examples/query.rs)。

## 结果语义

- `ok: true` 表示调用被接受；计算是否成功要看 `job.state`。
- Int64、UInt64 与 Decimal 在 JSON 中用字符串保留精度，schema 保留数值类型。
- 精度、覆盖范围、请求完成状态与展示截断分别表达。部分数据不会因为派生计算完成而变成完整数据。
- 查询绑定固定 Manifest 或 Result revision；分页游标固定版本，不会偷偷跟随最新结果。
- 等待、计算、扫描、结果存储和输出预算相互独立。引擎内存池上限不是整个进程的 RSS 上限。
- 本地文件一致性通过文件身份、大小和修改时间尽力检查，不等同于文件系统事务快照。

已有确定性测试与引擎对照，尚无已验证的 Agent 采用优势或性能优势。小数据上，
RowTrail 的进程、协议与持久化成本明显高于直接使用持久 DuckDB/DataFusion 会话。
测量方法和原始数字见 [基准说明](benchmarks/README.md)。
