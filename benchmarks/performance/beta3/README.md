# Beta.3 measurements and raw evidence

All records are retained, including unfavorable results. One Apple arm64 Mac,
macOS 26.6.2, 24 GiB RAM / 14 logical CPUs, Rust 1.94.0. Serial runs, no concurrent
local build, OS caches not flushed. Binary hashes are in each benchmark file.
These local binaries are separate from the verified native CI archives.
The top-level reports repeat all four benchmarks after the error-handling
fixes and CLI size adjustment (source d512eea). A subsequent fix only resolves
the demo executable path through macOS installation links; query/response code
and the runtime are unchanged. Its additional latency repeat is retained in
`post-link-fix-latency.json`. Complete prior series remain in `earlier/` and
`before-cli-size-fix/`; no trials were discarded.
Generated references and timing fields affect token counts between series.

## Actual tokenizer counts, not bytes divided by four

`agent-context.json` retains every request/response for seven trials per variant,
100,000 rows, 64 columns. The full/compact variants execute identical operations
and return identical typed answers/quality. Each trial opens/discovers, saves a
large intermediate with zero observed rows, aggregates, checks uniqueness,
reconnects, discovers the saved context and asks one follow-up. All answers match
an independent Python integer oracle. Follow-ups read zero original-source bytes.

| Variant | Response tokens (o200k_base) | Request tokens | Median ms |
|---|---:|---:|---:|
| RowTrail full | 4,753 | 998 | 73.28 |
| RowTrail compact | 3,438 | 1,038 | 71.88 |
| Direct DuckDB, retained in-memory connection/tables | 223 | 324 | 17.44 |
| Direct DuckDB, durable file and reopened connection | 223 | 324 | 34.38 |

Compact reduces response tokens **27.7%**, and the sum of the reported request /
response medians **22.2%**. Request tokens increase because the compact preference
is explicit. The `cl100k_base` response counts are 4,703 → 3,386 (28.0% fewer).
UTF-8 response bytes are 14,615 → 10,521 (28.0% fewer). Individual counters vary
with generated references, timestamps, metrics and timing.

A separate 64-field metadata probe returns 1,060 → 127 `o200k_base` tokens when
searching for the one revenue field (**88.0% fewer**). It is not charged to the
database baseline: both controls can query only relevant schema columns too.
The compact code-composed decision card is 986 tokens; it leaves operational
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
| 2M rows, save + ten queries + reconnect, 7 trials | 698.81 ms | 700.08 ms |
| Save component | 278.85 ms | 285.96 ms |
| Ten follow-ups component | 350.53 ms | 349.30 ms |
| 1M rows, five-query exploration, 7 trials | 146.32 ms | 144.91 ms |
| Cold startup, 11 trials | 18.40 ms | 18.60 ms |
| Warm scalar median, 1,089 samples each | 0.887 ms | 0.891 ms |
| Warm scalar p95 | 1.304 ms | 1.310 ms |

The follow-up task is 0.2% slower and exploration 1.0% faster in this series;
no general speedup is claimed.
The earlier series is retained in `earlier/`, including a **2,276 ms beta.2
startup outlier**. The final series has no comparable outlier. Eleven startup
samples do not justify a tail-latency improvement claim. The separate
million-row controls remain faster: beta.3-series DuckDB 69.70 ms, direct
DataFusion 37.13 ms. They retain in-memory intermediate tables.

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
| `earlier/` | Complete first series before the final MCP/client error fixes |
| `before-cli-size-fix/` | Complete second series before selecting CLI opt-level=2 |
| `cli-size-experiments.json` | Rejected size choices and selected one-codegen-unit, opt-level=2 CLI |
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

Linux CI also rejected the opt-level=3 CLI at 4,616,672 bytes (limit 4,500,000).
The final CLI alone uses opt-level=2 and one codegen unit; engine settings remain
unchanged. The first Linux size experiment built only the CLI, which uses a
different dependency feature graph; it is labeled separately and does not stand
in for the complete workspace release build. The retained native CI verifies the
actual shipped binaries and the unchanged size ceilings.

The complete Linux workspace comparison selects 4,451,824 bytes with one
codegen unit / opt-level=2. Eight units produced 5,704,120 bytes at opt-level=3
and 5,648,008 at opt-level=2. All configurations and hashes remain in the size
report. Native release validation, rather than this size-only experiment, gates
publication.

The first complete native CI candidate passed on its source checkout but its
macOS installed-symlink demo failed on the development machine: a source fallback
had hidden the wrong resource directory. That archive was not published.
`rejected-install-candidate.json` retains the failure and disposition; the
final native suite includes a distinguishable bundled-script regression.

The post-link-fix repeat has 11 cold starts and 1,089 warm scalar samples per
version; it is separate from the primary series above and retains all samples.

The downloaded final macOS CI archive also passes `native-macos-beta3.json` and
`native-macos-install.json` on macOS 26.6.2, including the real install symlink.
`native-demo-result.json` and `native-demo-report.md` retain a default 100K-row run
using those binaries; only its local directory prefix is replaced with
`<native-demo>`. Both platforms' hashes, sizes, all suites and Ubuntu 24.04
installation are in [the release proof](../../../docs/release-verification.json).

`upload-verification.json` checks all six GitHub assets against the verified CI
files before publication. `public-install.json` then tests the published tag's
default installer via anonymous network download, exact executable hashes, the
installed demo and existing handoff/numeric checks. Both passed.
