# Verification: alpha.8

[Home](../README.md) · [Agent contracts](agent-guide.md) · [Current limits](progress.md)

Release candidate: final repeated measurements and native artifact verification
are in progress. The [alpha.7 report](releases/alpha7-verification.md) and
[provenance](releases/alpha7-verification.json) remain archived.

The local candidate passes label/handoff, one/two/four-partition aggregate/join,
full-row parallel sort, byte-budget and cancellation checks. Final counts and
source hashes will be recorded before release. Preliminary positive and negative
trials are retained in [experiments](../benchmarks/performance/alpha8/experiments/).

## Native distribution

Publishing requires macOS arm64 and Ubuntu 22.04 x86_64 native CI, a repeat install
of the same Linux archive on Ubuntu 24.04, archive/binary checksum and size checks,
licenses, bundled guides/demo and a real schema-6 to schema-7 upgrade.

## Agent workflow evidence

The runnable example demonstrates bounded, exact result handoff. It is not a new
paired-agent latency/token study. Alpha.6 remains the latest paired pilot; all
12 answers were correct, but RowTrail was slower and used more cumulative input
tokens than persistent DuckDB.

## Reproduce

See [benchmark commands](../benchmarks/README.md), [engineering rules](../AGENTS.md)
and [the alpha.8 design](decisions/008-bounded-parallelism-and-handoff.md).
