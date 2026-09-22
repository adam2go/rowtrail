# Alpha.7: cheap durable observations and bounded reuse

Status: implemented and verified on native macOS/Linux release artifacts.

The product priorities remain agent-native contracts, a small native distribution,
and fast complete exploration. This iteration targets three measured costs:
manual code-level orchestration, tiny durable result publication, and repeated
reads of saved results. No model call or external dependency is added.

## Small Arrow parts in SQLite

Arrow IPC parts up to 128 KiB of encoded bytes start in a bounded memory buffer.
Crossing the limit spills once to the existing buffered file writer. Large Arrow
parts, prepared Parquet, and exports retain their file sync, rename, directory
sync, SQLite FULL commit, and acknowledgement ordering.

A small part is sent as bounded hex in the internal worker frame. The coordinator
checks its size and SHA-256, then inserts its descriptor and BLOB in the same
SQLite transaction. It acknowledges only after the FULL commit. There is no
volatile success path and no result data in the agent's response beyond the
requested observation. Jobs are still durably accepted before execution.

Fixed revisions and progressive checkpoints can refer to inline and file parts
in the same result. Readers verify and decode exactly the same bytes. Workers
open the workspace database read-only to fetch inline data; synthetic file paths
are internal object-store keys and are never materialized as empty files.

Metadata becomes schema 6. Opening schema 3/4/5 upgrades once, preserving old
files and revisions. Earlier runtimes refuse the upgraded store. This is an
intentional one-way storage change, not backward readability by alpha.6.

Encoded inline bytes count against result and workspace storage budgets. GC
first commits expiration, then deletes parts and their cascading BLOB rows.
SQLite may reuse freed pages without shrinking its file. Physical database/WAL
and filesystem allocation overhead remains outside the logical data quota.
Read metrics distinguish inline bytes from opened file bytes; neither represents
physical device I/O through the OS page cache.

## Exact source ranges and task-local verified cache

For non-checksummed external Parquet, a get_ranges call totaling at most 8 MiB
opens one file and reads the requested ranges in one blocking task. It reserves
all requested bytes before I/O, counts actual successful reads, and revalidates
source identity afterwards. It does not read intervening column gaps. Larger
requests retain the previous streaming/coalescing path. The bounded buffer is
outside the engine MemoryPool, as are the existing object-store buffers.

The previous single verified-part cache becomes a bounded LRU: at most 8 MiB of
encoded bytes and 128 entries. Several small compressed parts can now share the
same byte budget. Cache entries exist only inside one job's object store; a new
job verifies every loaded part again. Cache hits return the captured verified
bytes, never a fresh unchecked read. Eviction may cause another verified read.
The cache is not a query-result cache or a promise that every repeated scan fits.

## Code composition

Open and job responses expose a ready-to-use fixed binding. Suggested next
operations do not recommend rereading an already included complete observation.
The optional stdlib Python client adds open/query/binding/rows helpers. A query
submits once, waits within a bounded deadline, and requests at most one bounded
page when a wait response lacks an observation. It never replays a mutation or
turns a deadline into cancellation. Failed jobs retain their full responses.

The rows guard rejects partial source coverage, estimates, nonfinal/invalid
observations and output truncation. Raw observations remain available for an
agent to interpret those cases explicitly. No implicit full-table collection,
float conversion or automatic population inference is performed. Mechanical
operations can be composed in a single host code call without a model turn
between them. The CLI can print the client source locally with python-client;
Python remains optional and no package install or network fetch is necessary.

A shorter path is a design hypothesis until measured in representative agent
workflows. Deterministic replay and backend timings do not establish model-level
latency or token savings. Keep real-agent and engineering evidence separate.

## Experiments kept out of the default

Four DataFusion target partitions improved the million-row workflow from 235.06
ms to 181.64 ms in five alternating runs, while small input changed from 29.45 to
30.03 ms. However, the 32 MiB sort failed: parallel merge reservations exhausted
the pool before any result was published. The default remains one partition.
An eventual explicit or adaptive concurrency policy needs a broader join/sort and
memory-budget matrix; higher throughput alone does not justify this regression.
The direct-engine controls in that experiment remain single-partition/thread,
so their comparison with the four-partition variant is not an equal CPU budget.

Adaptive coordinator startup polling (1, 2, 4, 8, then 10 ms) gave inconsistent
results, including slower startup in the final candidate comparison. The original
10 ms polling stays. No cold-start improvement is claimed.

An initial 64 KiB inline threshold left a representative 66 KiB saved subset on
the file path. Raising the bounded threshold to 128 KiB covers it. An early trial
also exposed canonicalization of a synthetic inline path; that failed trial is
retained, and the corrected implementation treats frozen paths literally, including
spaces and glob characters. It does not silently discard a failed benchmark.

Faster commits exposed a worker-loss race: death after part commit but before ACK
could escape as a broken-pipe error and be labelled failed. Broken control pipes
now terminate/reap the worker and record interruption, preserving the committed
revision. A deterministic real-pipe regression closes the ACK reader before a
valid inline publication. Cancellation, quota and timeout finish rules still apply.

See the [verification report](../verification.md) and its experiment inventory for
raw timings, negative results, quality checks and release provenance.
