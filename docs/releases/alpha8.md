# RowTrail alpha.8 — faster exploration, discoverable saved work

Agents can save a useful subset, reconnect, find it by label and query the same
fixed result without returning the whole table through the model. Alpha.8 makes
that workflow faster and easier to compose, with no new external dependency.

## What changed

- **Fewer large-result commits.** Arrow part rotation uses encoded bytes already
  written plus conservative next-batch headroom. The 8 MiB encoded limit,
  immediate first preview, checksums, file sync and SQLite FULL commit ordering
  remain intact.
- **Resource-aware SQL parallelism.** Small inputs and tight engine pools stay
  serial. Larger scans use bounded targets; `--target-partitions` provides an
  explicit choice, with insufficient memory rejected before job acceptance.
- **Saved work is discoverable.** Optional `open`/`query` labels, indexed exact
  catalog filtering, row counts and bounded field hints support handoff in one
  catalog call plus one query. Labels are descriptive and nonunique; immutable
  revisions remain the authority.
- **A runnable introduction.** Native archives bundle agent guides, the optional
  stdlib client and a complete demo: generate 20,003 orders, save 385 refunds,
  reconnect and verify exact answers, including an ID above 2^53.
- **Broader Linux compatibility.** Build on Ubuntu 22.04 / glibc 2.35 and install
  the same archive on Ubuntu 24.04. The installer checks the actual executables
  and version before changing an existing installation.

## Measured on one Apple arm64 Mac

| Complete workflow, median ms | alpha.7 | alpha.8 |
|---|---:|---:|
| Million-row exploration, defaults; seven alternating trials | 234.26 | 144.22 |
| Million-row exploration, both one partition; seven trials | 234.39 | 193.83 |
| Million-row sort, 32 MiB engine pool; five alternating trials | 912.20 | 462.41 |

Default exploration takes about **38% less time**; the spill sort takes about
**49% less time**. Sort output drops from 43 parts to three, and every exported
ID is checked independently. Direct engine controls receive matching maximum
query targets and may retain intermediates; they remain faster than RowTrail.

There is a real tradeoff: the first 100-row page of a large saved result takes
**0.64 → 2.00 ms**, because larger parts still undergo whole-part verification.
Small calls and high-entropy workloads remain broadly unchanged. The engine pool
is not an RSS cap. These are local backend measurements, not a proven real-agent
latency/token advantage.

[Full method, raw samples, hashes and negative results](https://github.com/adam2go/rowtrail/blob/v0.1.0-alpha.8/docs/verification.md).

## Install and verify

Native downloads: **macOS arm64 19.23 MB**, **Linux x86_64 22.56 MB** (decimal,
compressed). Archive budget remains 30 MB. Runtime use requires no Rust, Python,
Node, Docker, model API key or external database.

```sh
curl -fsSL https://raw.githubusercontent.com/adam2go/rowtrail/v0.1.0-alpha.8/install.sh -o /tmp/rowtrail-install.sh
sh /tmp/rowtrail-install.sh
export PATH="$HOME/.local/bin:$PATH"
rowtrail --version
```

Both native build platforms pass **99 integration scenarios and eight Rust
tests**, including twelve subprocess publication-crash cases. The same Linux
archive passes installation and its demo on Ubuntu 24.04. Checksums, binary
architecture, test hashes, size budgets and 557 dependency notice files per
archive are independently verified.

[Native build and artifact provenance](https://github.com/adam2go/rowtrail/blob/v0.1.0-alpha.8/docs/release-verification.json).

**Upgrade:** metadata moves to schema 7. Existing fixed results and partial
checkpoints are preserved; old runtimes refuse the upgraded workspace. Stop the
coordinator and retain a complete pre-upgrade workspace copy if rollback matters.

## Help shape the next version

This is an Apache-2.0 engineering preview. Windows packages, remote sources,
interrupted computation continuation and progressive GROUP BY/filtering remain
unimplemented. The next useful contributions are independently reproduced agent
tasks, smaller projected integrity reads, easier integrations and smaller native
packages.

[Contribute](https://github.com/adam2go/rowtrail/blob/v0.1.0-alpha.8/CONTRIBUTING.md)
or [report a reproducible workload](https://github.com/adam2go/rowtrail/issues).
