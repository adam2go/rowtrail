# 002: Bounded persistence and agent sessions

Accepted for 0.1.0-alpha.2.

The alpha.1 exploration benchmark spent most of its time publishing small result
files. An engine batch of 1,024 rows caused one file sync, directory sync, SQLite
FULL commit and coordinator acknowledgement. A million-row materialization
created roughly a thousand files. Polling also added up to 5 ms before execution
and 10 ms before returning completion, even for trivial queries.

Keep Arrow IPC file storage and synchronous durable commits. Write multiple
engine batches to each staged file, targeting 4 MiB with an 8 MiB hard write
limit and at most 128 batches. Incoming batches are written and released; this
is not an in-memory whole-result cache. The engine batch size is 8,192 rows.
Large batches are sliced toward 2 MiB before writing. Oversized single values
still fail explicitly. Publish the first available preview immediately; seal
later files by byte count, batch count, 50 ms while awaiting input, or completion.
Dictionary-typed output (including nested dictionaries) uses independent batch
files because Arrow IPC files cannot replace a field dictionary between batches.
The final file may be smaller. Coalescing is not a promise of uniform file sizes.

Publication still orders file sync, rename, directory sync, metadata commit and
acknowledgement. Only committed, sealed files are readable. Cancellation can
lose uncommitted staging data but cannot retract an already published revision.
Worker control frames are written to completion before a cancellation handler
can send its terminal frame; the separate supervisor remains able to kill a
worker blocked on its pipe. A transport failure records confirmed shutdown only
after awaiting worker exit.

Use durable-state notifications instead of queue/completion/event polling.
Subscribe before checking SQLite state to prevent missed wakeups. Notifications
carry no authoritative data; startup recovery and SQLite remain the source of
truth. The queue uses a retained notification permit while the worker is busy.
The external cancellation supervisor retains its independent 10 ms tick.

For reads, verify and decode the same bounded byte buffer, avoiding a second
file read and a verification/reopen race. Account for each added JSON row once
instead of reserializing all previously returned rows. Final envelope size and
cursor semantics are still checked.

Expose sequential `Client::session()` and `rowtrail session` NDJSON envelopes.
A cancelled or failed RPC consumes its connection. Never automatically replay a
mutation after an ambiguous transport failure. Idempotency remains caller-owned.
CLI, session, MCP and SDK use the same request contracts and durable objects.

Use xz level 6 for release archives. This reduces download size without changing
executable code or runtime performance. Keep both binaries and license notices
in one native archive. Enforce measured distribution growth budgets in packaging.
No installer requires Python, Node, Docker or a query engine installation.

No concurrency, unsafe durability mode, global cache, additional query engine,
model client, or GUI is introduced. Dataset parallelism, all-input checksum
verification, garbage collection, and full RSS enforcement remain separate work.
