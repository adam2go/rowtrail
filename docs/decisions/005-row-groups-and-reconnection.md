# Alpha.5: row-group observations and workspace reconnection

Status: implemented and verified for alpha.5. See [verification](../releases/alpha5-verification.md)
and [native artifact provenance](../releases/alpha5-verification.json).

`analyze` defaults to `fragment_unit:parquet_row_group` and
`checkpoint_interval_ms:50`. Explicit `manifest_file` plus interval 0 preserves
the alpha.4 per-file checkpoint behavior. Both paths use public DataFusion
ParquetAccessPlan / DataSourceExec APIs, one execution context per job, a bounded
metadata cache (min(memory budget / 8, 8 MiB)), and the existing restricted,
counting, checksum-verifying store. No additional dependency or model call.

The first complete fragment is published; later complete prefixes are coalesced
by the interval. The final prefix is always sealed. `preview:none` writes one
final checkpoint. Neither elapsed time nor a partially decoded row group can
advance committed coverage. Empty files advance completed_files but contribute
zero row groups. Totals can be unknown for older prepared manifests until read;
final coverage has an exact total. A single enormous row group still requires a
complete scan before its first aggregate checkpoint.

Coverage reports completed_fragments, total_fragments, completed_files,
total_files and processed_rows in frozen file/row-group order. Average keeps
alpha.4's Decimal128(38,6) truncation semantics. Checkpoints are replacement rows,
not appendable answers. Coalescing leaves existing immutable revisions intact.

Store schema 5 adds analysis_progress. Publication validates monotonic coverage
and completion checks the last durable prefix, independently of part count.
Schema 3/4 upgrades mark old serialized analyze requests as manifest_file with
interval 0. Old result revisions remain readable; older runtimes reject schema 5.
This does not add saved accumulator states or interrupted-job continuation.

`workspace summary` is a metadata-only catalog with bounded rows/bytes, optional
dataset/job/result filters and a workspace/filter-bound cursor. Membership uses
first-page rowid upper bounds; values are observed per page. New objects do not
appear halfway through pagination. Stored validity is explicitly not a fresh
filesystem validation. Result bindings always name a fixed revision; expired
objects remain tombstones. It adds no separate session/evidence object model.

`guide` and `mcp-config` print JSON without starting the coordinator. MCP tool
descriptions and typed error hints explain the existing contracts. Configuration
generation does not modify host files. No native Tasks or automatic host resume
is claimed.

Engineering correctness and local repeated timings remain model-free. Optional
paired agent experiments run outside the product, with the same model/settings,
synthetic data and a persistent Python environment for both RowTrail and DuckDB.
The baseline may materialize/reuse tables. Retain failures, tool responses,
model usage and separate source/result counters; these small experiments do not
establish broad adoption or universal speed advantages.
