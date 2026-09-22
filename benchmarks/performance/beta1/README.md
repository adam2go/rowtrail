# Beta.1 performance evidence

Final production code: `cbde5d1556dacf7506e9afb3e57083183eb3f927` (the executable
code is identical to `2852e27`; the later commit extends the native scan-budget
regression). Measurements use local release binaries; native release artifact
hashes are verified separately in [release provenance](../../../docs/release-verification.json).
Each raw series records its own executable hashes. No external dependency was
added to the product.

One Apple arm64 Mac, macOS 26.6.2, 24 GiB RAM, 14 logical CPUs. Fresh workspaces,
serial alternating version order, no concurrent local compilation or timed jobs.
OS caches are not flushed. These are deterministic backend tasks without model
calls; neither time nor response bytes establishes an agent-level advantage.

| File | Workload and repetitions per version |
|---|---|
| [followup-2097152.json](followup-2097152.json) | Save a 2M-row source subset, ten follow-ups, reconnect/strict label lookup and one resumed query; 7 trials. Python integer/Decimal oracle. |
| [exploration-16384.json](exploration-16384.json) / [exploration-1048576.json](exploration-1048576.json) | Five-query exploration; 7 trials, persistent DuckDB/DataFusion controls. |
| [resources.json](resources.json) | 1M-row full sort with a 32 MiB engine pool, export and independently verify every ID; 5 trials, sampled RSS. |
| [latency.json](latency.json) | 11 fresh-workspace sessions and 100 queries per session; first query separated, 1,089 warm samples per version. |
| [paging-1048576.json](paging-1048576.json) | Two-column pages from a wide 1M-row saved result; 100/1,000/10,000 requested rows, 21 trials each. |
| [entropy.json](entropy.json) | High-entropy numeric save and reuse; 7 trials with an independent oracle. |
| [verification.json](verification.json) | Local suite inventory and hashes of the final raw records. |
| [native-macos-alpha9.json](native-macos-alpha9.json) | Fourteen new scenarios rerun against the actual macOS CI archive on the local Mac. |
| [upgrade-native-macos.json](upgrade-native-macos.json) / [numeric-upgrade-native-macos.json](numeric-upgrade-native-macos.json) | Published alpha.8 to actual beta.1 native archive, fixed revisions and invalid legacy values. |
| [install-native-macos.json](install-native-macos.json) | Actual native package install, checksum rejection, printable helper and bundled demo. |
| [public-install.json](public-install.json) | Public tag installer, actual network download/hash, cross-TMPDIR label handoff, SUM overflow and bundled demo after publication. |
| [experiments/](experiments/) | Earlier candidates, a failing legacy-provenance test, startup regression, first-invocation outlier and checked-SUM experiments. |

Full methods, benefits, costs and native acceptance: [verification report](../../../docs/verification.md).

## Reproduce

Keep alpha.8 and beta.1 executable pairs in separate directories. Build the
current version with the pinned toolchain and `scripts/cargo-local.sh build
--release --locked`. For example:

```sh
python3 benchmarks/followup.py --variant alpha8=/path/to/alpha8 --variant beta1=target/release --repeats 7 --output /tmp/followup.json
python3 benchmarks/latency.py --variant alpha8=/path/to/alpha8 --variant beta1=target/release --repeats 11 --warm-queries 100 --output /tmp/latency.json
```

Use the scripts' `--help` for other workloads. Direct-engine controls, resources
and entropy generation use a development environment with DuckDB 1.5.5; the
product does not. `python3 benchmarks/plot_beta1.py` regenerates the chart using
Matplotlib. Run timing workloads serially and retain all samples.
