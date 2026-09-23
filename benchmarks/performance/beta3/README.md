# Beta.3 measurements and raw evidence

All records are retained, including unfavorable results. One Apple arm64 Mac,
macOS 26.6.2, 24 GiB RAM / 14 logical CPUs, Rust 1.94.0. Serial runs, no concurrent
local build, OS caches not flushed. Binary hashes are in each benchmark file.
These local binaries are separate from the eventual verified native CI archives.

## Actual tokenizer counts, not bytes divided by four

`agent-context.json` retains every request/response for seven trials per variant,
100,000 rows, 64 columns. The full/compact variants execute identical operations
and return identical typed answers/quality. Each trial opens/discovers, saves a
large intermediate with zero observed rows, aggregates, checks uniqueness,
reconnects, discovers the saved context and asks one follow-up. All answers match
an independent Python integer oracle. Follow-ups read zero original-source bytes.

| Variant | Response tokens (o200k_base) | Request tokens | Median ms |
|---|---:|---:|---:|
| RowTrail full | 4,706 | 986 | 72.69 |
| RowTrail compact | 3,461 | 1,043 | 72.14 |
| Direct DuckDB, retained in-memory connection/tables | 223 | 325 | 18.12 |
| Direct DuckDB, durable file and reopened connection | 223 | 325 | 34.60 |

Compact reduces response tokens **26.5%**, and the sum of the reported request /
response medians **20.9%**. Request tokens increase because the compact preference
is explicit. The `cl100k_base` response counts are 4,652 → 3,401 (26.9% fewer).
UTF-8 response bytes are 14,587 → 10,521 (27.9% fewer). Individual counters vary
with generated references, timestamps, metrics and timing.

A separate 64-field metadata probe returns 1,067 → 138 `o200k_base` tokens when
searching for the one revenue field (**87.1% fewer**). It is not charged to the
database baseline: both controls can query only relevant schema columns too.
The compact code-composed decision card is 984 tokens; it leaves operational
events in code. Other database programs can also compose operations this way.

Tokenization uses **tiktoken 0.12.0**, `o200k_base` and `cl100k_base`, each actual
JSON message separately. [Tokenizer API](https://github.com/openai/tiktoken).
Counts exclude system prompts, tool definitions, chat framing, reasoning, caching
and repeated conversation input. They are not model invoices, a universal
percentage saving, or a new same-agent trial. RowTrail's full quality, provenance
and durable references cost more context than the deliberately minimal DB wrapper.
The baseline is not forced to dump rows or discard intermediates. RowTrail has
nine native requests; DuckDB uses ten SQL statements, not ten model turns.

## Existing performance remains broadly stable

| Metric | beta.2 | beta.3 |
|---|---:|---:|
| 2M rows, save + ten queries + reconnect, 7 trials | 683.78 ms | 689.54 ms |
| Save component | 271.97 ms | 275.51 ms |
| Ten follow-ups component | 347.80 ms | 347.66 ms |
| 1M rows, five-query exploration, 7 trials | 144.62 ms | 146.23 ms |
| Cold startup, 11 trials | 18.46 ms | 18.55 ms |
| Warm scalar median, 1,089 samples each | 0.890 ms | 0.890 ms |
| Warm scalar p95 | 1.354 ms | 1.329 ms |

The larger tasks regress about 0.8% / 1.1%; no general speedup is claimed.
The beta.2 startup series contains a **2,276 ms outlier**, retained in full. Eleven
startup samples do not justify a tail-latency improvement claim. The separate
million-row controls remain faster: beta.3-series DuckDB 69.26 ms, direct
DataFusion 36.83 ms. They retain in-memory intermediate tables.

## Reproduce

```sh
python3 -m venv benchmarks/local/context-venv
benchmarks/local/context-venv/bin/pip install -r benchmarks/requirements-context.txt
benchmarks/local/context-venv/bin/python benchmarks/agent_context.py --rows 100000 --repeats 7
python3 benchmarks/latency.py --variant beta2=/path/to/beta2 --variant beta3=target/release --repeats 11
python3 benchmarks/followup.py --variant beta2=/path/to/beta2 --variant beta3=target/release --rows 2097152 --repeats 7
benchmarks/local/context-venv/bin/python benchmarks/compare_matrix.py --variant beta2=/path/to/beta2 --variant beta3=target/release --rows 1048576 --repeats 7 --output benchmarks/local/exploration.json
rowtrail demo --directory ./rowtrail-demo
```

Benchmark dependencies are not shipped in RowTrail. The demo requires stdlib
Python only and makes no network/model calls. The isolated benchmark environment
may download tokenizer vocabulary files on first use, outside measured timing.

## Inventory

| File | Purpose |
|---|---|
| `agent-context.json` | All 28 raw transcripts, correctness, timings and both tokenizer counts |
| `latency.json` | 11 alternating cold starts and 1,089 warm queries per binary |
| `followup.json` | All 14 two-million-row durable-follow-up trials, I/O and independent oracle |
| `exploration-1m.json` | All 14 RowTrail/DuckDB/DataFusion five-query comparisons |
| `cli-size-experiments.json` | Rejected `opt-level=s` growth and selected one-codegen-unit CLI |
| `demo-result.json`, `demo-report.md` | Actual 100K-row offline demo, local base path replaced with `<demo>` |
| `demo-handoff-report.md` | Readable portable branch with bounded previews and original-input limits |
| `demo-receiver.json`, `demo-recipe-run.json` | Separate recipient and explicit recomputation from included intermediate |
| `local-beta3.json`, `local-install.json`, `local-upgrade-beta2.json` | Local feature, real archive installation and public beta.2 compatibility checks |
| `raw-sha256.json` | SHA-256 inventory of published evidence, excluding itself |

The first benchmark-harness smoke run exposed DuckDB's positional parameter
ordering inside COPY; explicit escaped SQL literals fixed fixture generation.
This happened before any measured trials. It was not a RowTrail query defect or
a selectively removed trial. `opt-level=s` also increased CLI size and was
rejected; the engine's optimization settings were never reduced.
