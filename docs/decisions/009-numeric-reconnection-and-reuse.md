# Checked sums, stable connections and bounded follow-up work

Status: implemented for beta.1; final native acceptance and publication pending.
The work started as alpha.9. The beta label follows local numeric, connection,
legacy-upgrade and full-regression acceptance, with native release gates retained.

## Numeric policy v1

`accuracy: exact` describes sampling. It does not promise arbitrary-precision
SQL arithmetic. New jobs declare `quality.numeric.policy: rowtrail-numeric-v1`;
`other_sql: engine_semantics` and per-input lineage remain visible. Missing policy
on an old revision means unknown, never retroactive certification. Export/reuse
preserve that provenance. Frozen revision contents are not rewritten on upgrade.

RowTrail registers a checked SUM for Int64, UInt64 and Decimal32/64/128/256.
Small integers retain DataFusion coercion; output types/Decimal scale retain its
SQL typing. State uses checked i256 arithmetic and checked UInt64 row counts.
Partial merge carries the wide state, not a prematurely narrowed answer. Final
scalar/group/window evaluation checks the declared output range, so partition or
batch boundaries do not cause Int64 overflow when the complete sum fits. i256
intermediate exhaustion itself is an error; this is not unbounded arithmetic.
DISTINCT uses accounted distinct-value state; sliding retraction retains duplicate
counts. Float SUM retains engine semantics. Interval/duration SUM requires an
explicit numeric-unit conversion rather than silently wrapping.

Decimal values are checked against their declared precision before publication,
presentation and export, including old materialized values. This prevents the
Arrow formatter from displaying a precision-invalid Decimal as a different
number. These checks cannot reconstruct an already-wrapped legacy integer or
certify other SQL expressions (including AVG, scalar arithmetic and casts).
A wider explicit Decimal can help only within its declared range.

Overflow fails the durable job with `ARITHMETIC_OVERFLOW`, operation, type,
expression and recovery details. No invalid batch is published; an earlier valid
preview remains an explicitly nonfinal immutable revision. It is not a successful
final answer. Metadata schema 8 prevents an older runtime from accepting jobs
under the new numeric policy. Preserve a stopped full workspace before upgrading.

## Endpoint identity and transport

The socket uses a short private UID/workspace-hash directory under `/tmp`,
independent of TMPDIR and workspace path length. A coordinator holding the
workspace lock atomically publishes an owner-private descriptor containing its
workspace, socket, store ID, PID, API and runtime versions. Clients check file
ownership/permissions/type/size and live peer UID, then compare handshake identity
and exact runtime version. A descriptor is discovery metadata, not authority over
a live peer. Stale sockets are replaced only by the workspace-lock owner.

Connection failures retain typed codes, log location and recovery hints across
CLI, NDJSON and MCP. A failed handshake cannot submit jobs. A failed/cancelled RPC
consumes its session; no possibly accepted mutation is replayed. Existing old
coordinators must be allowed to finish/exit before upgrade; the client does not
kill active work to take over a workspace.

## Small client and repeated use

The optional Python helper uses only the standard library. Prepare/inspect/export
submit once and wait within a caller deadline; exceptions keep the job response.
Strict label lookup requires exactly one usable match; duplicates, missing,
expired and nonfinal entries are explicit errors. It never guesses the newest.
Paging requires total rows, response-envelope bytes and page limits. EOF finishes
normally; a budget ending before EOF raises a resumable exception. Raw typed
strings and schema remain the default; optional Python int/Decimal conversion
never uses float for those values.

Saved Arrow results are already known IPC files with parts bounded to 8 MiB.
An experimental source skips format sniffing and keeps each part whole while
allowing separate files to run in parallel. This targets duplicate whole-part
verification caused by byte-range repartitioning. The existing job-local 8 MiB
verified-byte cache, SHA-256, scan reservation, publication fsync/SQLite FULL,
commit-before-ACK and cancellation ordering stay intact. Each new job revalidates.
Retain the experiment only if complete workflow and resource tests support it.

## Acceptance and beta decision

Require numeric boundaries (scalar/group/DISTINCT/window/real partition merge),
malformed Decimal rejection, fixed old results and schema upgrade, TMPDIR changes,
stale and mismatched endpoints, multi-session startup, client total limits and
structured errors, and all previous durability/cancellation scenarios. Measure
save-large-subset plus ten follow-up queries plus reconnect/label discovery using
an independent integer oracle, alternating repeated trials and native I/O costs.
Keep regressions and single-machine limits visible. Native macOS/Linux artifacts
must pass package-size, hash, license and installation checks before publication.

A beta label means these bounded CLI/MCP/local-workspace contracts have survived
that gate. It does not imply stable 1.0 metadata, signed packages, general SQL
arithmetic certification, Windows support or proven model-level efficiency gains.
