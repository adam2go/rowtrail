# Alpha.6: bounded result encoding and observation cost

Status: implemented; measurements and native release verification are recorded
in [verification](../verification.md).

Result Arrow parts whose first batch occupies at least 64 KiB use standard IPC
Zstd level 1. Small observations and progressive aggregate checkpoints stay plain
IPC. Arrow stores incompressible buffers without compression when encoding would
expand them. Compression still costs CPU; this is not a promise that every input
gets faster or smaller. Prepared Parquet and external export formats are unchanged.
The pinned DataFusion dependency already enables both Zstd and LZ4 IPC support;
we enable Arrow's compression feature explicitly without adding packages.

A staged writer holds a 64 KiB file buffer, hashes exactly the bytes it accepts,
checks the encoded byte budget before accepting a write, flushes the buffer, then
syncs the file before asking the coordinator to publish it. Rename, directory
sync, SQLite FULL commit and acknowledgement keep their previous order. Checksums
cover stored encoded bytes. Reads verify and parse the same bytes; there is no
cross-job cache that skips verification. Source/result I/O remain separate.

Arrow parts target 6 MiB of estimated input array memory, retain the 8 MiB encoded
hard limit and 128-batch bound, and release each input batch after writing.
Prepared Parquet retains its 4 MiB target. A first available preview is still
published immediately; dictionary batches remain independent. The 50 ms flush
rule is unchanged. A target is not a guaranteed part size: schema/IPC metadata,
large values and conservative shared-array accounting can affect encoded size.
The engine memory budget still does not bound process RSS, writer/codec buffers
or the existing single verified-part cache. Resource experiments report sampled
RSS separately and include an above-pool sort with spill.

Page construction formats each column once per batch and accounts exactly for
row bytes, decimal count widths, boolean length and the hex cursor. It no longer
serializes schema/quality and recreates a cursor for every candidate row. The
final complete response still goes through the existing byte-limit check. Tests
compare the arithmetic with JSON serialization at digit boundaries and paginate
Unicode/escaped names, nulls, Decimal values and exact large integers.

Metadata remains schema 5. Alpha.5's existing IPC decoders can read the compressed
files; a real two-version probe verifies both directions and fixed partial
revisions. Opening schema 3/4 still performs the previously documented upgrade.

The optional standard-library bridge provides bounded mechanical waiting,
on-demand local schema discovery and a compact job projection. Full responses
remain available in caller code; the projection retains the whole observation,
fixed binding, state, error and next actions. It never repairs or hides partial
coverage, truncation or failed jobs, makes no model calls and never retries a
mutation. A helper deadline returns the last state and does not cancel execution.
This is an example integration, not a new wire format or required Python runtime.

Experiments retain plain/buffered/LZ4/Zstd-fast/Zstd-level-1 candidates, an
interleaved high-entropy workload, full five-query exploration, paging and spill.
Smaller stored bytes are distinct from wall-time gains. The external-agent pilot
uses a shorter published bootstrap and optional helpers; comparing its model wall
time with alpha.5 is observational, not a controlled latency experiment. DuckDB
profiling covers all statement types and finalizes scalar fetches with one-row
lookahead; incomplete profiles remain unmeasured instead of becoming zero.
