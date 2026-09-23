# Beta.2 measurements

These are local release-build measurements, independent of the native CI builds
used for published archives. Every JSON retains binary hashes and raw repeats.
No external model is called; no token or adoption advantage is inferred.

| File | Trials / purpose |
|---|---|
| `answer-budget.json` | 7 alternating repeats × 100 scalars, each at 2 KiB and 8 KiB; complete answer latency, calls and response bytes. |
| `followup.json` | 7 alternating repeats on 2,097,152 rows: save a subset, ten follow-ups, reconnect. Independent Python integer/Decimal oracle. |
| `exploration-1m.json` | 7 alternating five-query explorations; persistent DuckDB 1.5.5 and direct DataFusion controls keep intermediates. |
| `latency.json` | 11 fresh workspaces per version; 100 queries each; cold/first/warm distributions. |
| `paging.json` | 21 alternating reads each for 100 / 1,000 / 10,000 rows; every returned ID checked. |
| `analysis-workflow.json` | 5 full beta.2 workflows on 131,072 rows: independent snapshot, before/after, keyed diff, check, report/package with inputs, fresh import, explicit recipe rerun. Independent oracle. |
| `resources.json` | 5 alternating million-row 32 MiB spill runs; independently verify every exported ID. |
| `earlier/` | Two earlier local candidate runs, retained including a 215 ms warm-latency outlier. Candidate hashes and slightly different helper implementations remain explicit. |
| `raw-sha256.json` | SHA-256 of raw JSON records, excluding itself. |

Method: one Apple arm64 Mac, macOS 26.6.2, 24 GiB / 14 logical CPUs, Rust 1.94.0.
Serial alternating order, fresh workspaces, OS caches not flushed. No concurrent
local builds or timed workloads. Medians of components need not add to median
total. MB is decimal; MiB is binary. Engine pool bounds are not RSS bounds.

The original full-workflow benchmark oracle incorrectly assumed `upper()` would
change Chinese region names and ignored Decimal precision widening. Its query
was corrected to explicitly change names and preserve the intended Decimal type;
no product arithmetic change was made to satisfy that oracle. The final diff
only compares common identically typed columns, as documented.

The latest complete task is broadly stable across candidate runs: small changes
in either direction, not evidence of a general engine speedup. A tiny answer
benefits from prioritizing its observation over optional job metrics. Complete
metrics remain available through status. Direct engine SQL remains faster.
Import spends additional I/O on verification, a private copy, managed snapshot,
schema/count validation. The package contains about 30.7 MB of **data** in this
fixture; this is separate from the native **installation** archive budget.

## Reproduce

Preserve the beta.1 native binary pair in a separate directory. Run one command
at a time without a local build or another timed workload in parallel:

```sh
python3 benchmarks/answer_budget.py --variant beta1=/path/to/beta1 --variant beta2=target/release --repeats 7
python3 benchmarks/followup.py --variant beta1=/path/to/beta1 --variant beta2=target/release --rows 2097152 --repeats 7
python3 benchmarks/compare_matrix.py --variant beta1=/path/to/beta1 --variant beta2=target/release --rows 1048576 --repeats 7 --output benchmarks/local/exploration.json
python3 benchmarks/latency.py --variant beta1=/path/to/beta1 --variant beta2=target/release --repeats 11
python3 benchmarks/paging_matrix.py --variant beta1=/path/to/beta1 --variant beta2=target/release --repeats 21
python3 benchmarks/analysis_workflow.py --rows 131072 --repeats 5
python3 benchmarks/resource_matrix.py --variant beta1=/path/to/beta1 --variant beta2=target/release --repeats 5
python3 benchmarks/plot_beta2.py
```

Only benchmark controls need DuckDB 1.5.5; plotting needs Matplotlib. Neither is a
product dependency. Native archives bundle the stdlib-only analysis demo.
