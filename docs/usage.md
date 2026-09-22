# Usage and building from source

For native installation, start with the [README](../README.md#install).
The commands below cover source builds and manual exploration; use the
[agent guide](agent-guide.md) for programmatic integration. Run build and example
commands from the repository root.

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

For a complete demo requiring no input download, run
`python3 examples/quickstart.py --rowtrail ./target/release/rowtrail` after building,
or use the same script bundled inside an extracted release archive with
`--rowtrail /absolute/path/to/rowtrail`. It generates 20,003 CSV rows, discovers
fields, chooses a region from exact aggregates, saves a labeled branch, reconnects
and verifies all answers with Python integers. It prints the retained workspace
path; the coordinator exits after 60 idle seconds and its results stay on disk.

```sh
./target/release/rowtrail-runtime fixtures --directory /tmp/rowtrail-data --rows 16384
./target/release/rowtrail open /tmp/rowtrail-data/small.parquet
```

The JSON response includes `dataset_ref` and `manifest_ref`. Use both to freeze
the input (replace the example IDs with the returned IDs):

```sh
rowtrail query --bind orders=ds_ID@mf_ID \
  --label 'totals by region' \
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
test [fixture manifest](../tests/fixtures/manifest.json) shows the schema format.

For a runnable workflow that passes fixed references between operations and
reports the original/result read counts, use the [composition example](../examples/explore.py):

```sh
python3 examples/explore.py /tmp/rowtrail-data/many.parquet --rowtrail ./target/release/rowtrail
```

Python is only needed for this optional example.

Find saved work without inspecting each opaque ID:

```sh
rowtrail workspace summary --kind result --label 'totals by region'
```

Labels are optional descriptions (up to 256 UTF-8 bytes), not unique names or
mutable aliases. Use the returned fixed binding. Counts and bounded schema hints
help selection; omitted fields and stored validity are explicit.

SQL automatically keeps small/tight-memory work serial and permits more target
partitions for larger inputs. Use `query --target-partitions 1` to pin serial
planning. Other explicit targets (2..8) require at least 64 MiB of engine pool
per partition; set `execution.memory_bytes` through JSON for larger pools.
`job.metrics.target_partitions` reports the choice. It is not a thread or RSS cap.

## CLI, MCP, and Rust client

All entries use the same request types, durable jobs, and result store.
For programmatic composition, keep one `rowtrail session` process open. Send one
complete request envelope per line and receive one response envelope per line:

```sh
printf '%s\n' '{"api_version":"1","request_id":"example","method":"query","params":{"bindings":{},"sql":"SELECT 42 answer","execution":{"wait_ms":1000}}}' | rowtrail session
```

The [standard-library Python bridge](../examples/session_client.py) uses this entry;
[the complete example](../examples/explore.py) reuses it across all exploration steps.
Sessions are sequential; use another connection to control a job while a wait is
pending. Closing a connection does not cancel accepted jobs. An interrupted RPC
is never silently replayed. See the [agent guide](agent-guide.md).


```sh
rowtrail schema query
rowtrail doctor
rowtrail --workspace /absolute/private/workspace mcp
rowtrail events --job job_ID --follow --jsonl
rowtrail job cancel job_ID
```

Relative source and export paths resolve in the calling client’s working directory.
The local wire protocol requires absolute paths.

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
the query engine. `Client::session().await?` opens a reusable, checked connection.

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

## Demand-driven profiles and preparation

```sh
rowtrail inspect mf_ID --columns amount,units --checks null_count,min_max
rowtrail inspect mf_ID --columns region --checks top_k --top-k 10
rowtrail inspect res_ID --revision 3 --checks head --max-rows 5
rowtrail prepare ds_ID --manifest mf_ID --wait-ms 1000
rowtrail workspace usage
rowtrail workspace configure --quota-bytes 2147483648
rowtrail pin res_ID
rowtrail release res_ID
rowtrail workspace gc           # dry run
rowtrail workspace gc --apply   # collect released, unprotected managed data
```

`prepare` returns proposed dataset/manifest references; wait for completion before
binding them. `job.prepared` repeats those references in later status/wait calls.
For full execution budgets, use `call inspect` or `call prepare` with JSON from
`rowtrail schema METHOD`. `open` never performs a profile or automatic conversion.
Release all unused branch results before GC; retained children protect ancestors.
The quota excludes SQLite, logs, external files and RSS. See the
[contract guide](agent-guide.md) for scope and error handling.

Run the complete generated-data example from a source checkout:

```sh
python3 examples/prepare_explore.py --rowtrail "$PWD/target/release/rowtrail" \
  --workspace /tmp/rowtrail-preparation-example --output /tmp/rowtrail-example.parquet
```

The final Parquet and quality sidecar remain after the example collects its own
intermediate data. The destination must not already exist.

## Progressive aggregate snapshots

```sh
rowtrail schema analyze
rowtrail analyze --request aggregate.json
python3 examples/progressive.py /path/to/parquet-directory --column amount \
  --stop-after-files 3
```

The JSON shape and supported numeric types are in the [agent guide](agent-guide.md).
The stop threshold is a caller policy; cancellation can race with another completed
file, so inspect the returned job state and actual coverage. A partial checkpoint
is a reusable exact answer over that file prefix, not an estimate over unseen data.
