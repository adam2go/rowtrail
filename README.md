# RowTrail

[简体中文](README.zh-CN.md) · [Releases](https://github.com/adam2go/rowtrail/releases)

**Let every step of an agent's data exploration continue.**

RowTrail is a headless local data tool for agents. Open CSV or Parquet, run
read-only SQL, keep a result, and ask the next question against a fixed revision.
Results stay on disk; the agent reads bounded observations with explicit types,
coverage, source consistency, and completion state.

The runtime makes **zero model calls**. Your agent decides what to investigate
and when the evidence is sufficient.

## Status

Early engineering preview under Apache-2.0. The first exact exploration loop is
implemented. Progressive sampling, automatic host resume, native MCP Tasks,
retention/GC, and remote sources are still on the roadmap. See
[verified capabilities and limitations](docs/progress.md).

There is no established performance or agent-adoption advantage yet. Benchmarks
include the cost of the worker, durable results, and protocol.

## Build

Initial targets are macOS and Linux. Rust 1.94.0 and a native C toolchain are
required to build. End users of release binaries do not need Rust, Python, Node,
Docker, or an external database.

```sh
cargo build --release --locked
```

On a Mac with an independently installed Command Line Tools toolchain:

```sh
./scripts/cargo-local.sh build --release --locked
```

Keep `rowtrail` and `rowtrail-runtime` together in a directory on your `PATH` when installing. Only the latter
links DataFusion. The first data operation starts a coordinator automatically;
help and schema discovery do not. The coordinator exits after 60 idle seconds. Successful jobs reuse its worker;
failed or cancelled jobs stop that worker before a replacement is started.

## Try the exact exploration loop

```sh
./target/release/rowtrail-runtime fixtures --directory /tmp/rowtrail-data --rows 16384
./target/release/rowtrail open /tmp/rowtrail-data/small.parquet
```

The JSON response includes `dataset_ref` and `manifest_ref`. Use both to freeze
the input (replace the example IDs with the returned IDs):

```sh
rowtrail query --bind orders=ds_ID@mf_ID \
  --sql 'SELECT region, SUM(amount) AS total FROM orders GROUP BY region'
```

A quick query returns an observation. Otherwise its stable `job_id` lets your
program wait or do other work:

```sh
rowtrail job wait job_ID --wait-ms 1000
rowtrail read res_ID --revision 2 --max-rows 20
```

Bind a saved result instead of reading and re-creating it in another script:

```sh
rowtrail query --bind saved=res_ID@2 \
  --sql 'SELECT * FROM saved WHERE total > 0'
rowtrail export res_ID --revision 2 --format parquet --output ./result.parquet
```

Exports include a `.rowtrail.json` sidecar with source quality and type metadata.
CSV input can use `--schema schema.json` for precise types such as Decimal. The
test [fixture manifest](tests/fixtures/manifest.json) shows the schema format.

For a runnable workflow that passes fixed references between operations and
reports the original/result read counts, use the [composition example](examples/explore.py):

```sh
python3 examples/explore.py /tmp/rowtrail-data/many.parquet --rowtrail ./target/release/rowtrail
```

Python is only needed for this optional example.

## CLI, MCP, and Rust client

All entries use the same request types, durable jobs, and result store.

```sh
rowtrail schema query
rowtrail doctor
rowtrail --workspace /absolute/private/workspace mcp
rowtrail events --job job_ID --follow --jsonl
rowtrail job cancel job_ID
```

Complex requests can be passed as JSON with `query --request query.json` or
`call METHOD --request -`. An optional `--idempotency-key KEY` makes retried
creation requests refer to the same job. Reusing the key for a different
calculation fails. Waiting and output budgets can change without creating a job.

MCP launch configuration uses the absolute path to `rowtrail`, arguments
`["--workspace", "/absolute/private/workspace", "mcp"]`, and stdio transport.
Ordinary tool results return the same JSON envelope as the CLI. Native Tasks and
automatic model resumption are not currently advertised.

Rust applications can depend on `rowtrail-client` and `rowtrail-contracts` in
this workspace. Set `ROWTRAIL_RUNTIME` to the executable path when the runtime
is not beside the host executable. `Client::call` is async and does not import
the query engine.

## Semantics that matter

- `ok: true` means the operation was accepted. Inspect `job.state` to determine
  whether computation completed. A status request can successfully return a
  failed job.
- Int64, UInt64, and Decimal values are JSON strings, with numeric types retained
  in schema. This protects values above JavaScript's exact integer range.
- Precision, coverage, completion, and display truncation are separate. Exact
  arithmetic on a partial input does not become a complete original-data answer.
- Each pagination cursor fixes a revision and projection. It never follows a
  changing result head implicitly.
- Wait time does not cancel work. Computation, scan, result, spill, and output
  budgets have separate meanings. Engine memory accounting is not total RSS.
- Local mutable files use identity/size/mtime checks, providing best-effort
  consistency. They are not transactional filesystem snapshots.
- Cancellation becomes terminal after worker exit is observed. Worker or
  coordinator crashes leave interrupted jobs, not resumable SQL instructions.

## Verify

```sh
cargo test --workspace --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo build --locked
python3 tests/integration/exploration.py
python3 scripts/mcp_probe.py target/debug/rowtrail
```

Tests use deterministic local fixtures and no model calls. Large fixtures are
generated locally and excluded from Git. See [progress](docs/progress.md),
[dependency decisions](docs/decisions/M0-dependencies.md), and
[benchmark data](benchmarks/m0.json).

## Contributing

Preserve exact types, fixed references, bounded output, and observable failure.
Add a reproducible test for changes to data or task semantics. Keep engine
dependencies out of the CLI/client. Include measurements when claiming lower
cost. Contributions are licensed under [Apache-2.0](LICENSE).
