# 011 — Spend context on evidence, keep execution outside the model

Status: accepted and verified in 0.1.0-beta.3.

An agent using a large table needs to discover relevant columns, keep intermediates
outside its context, distinguish a complete answer from a preview, and recover the
purpose and inputs of saved work. Beta.2 supplies the durable foundation but still
returns substantial repeated job metadata and requires manual context assembly.

## Additive native interface, unchanged storage

- A request-level `response_mode: "compact"` removes operational job metrics,
  timestamps and duplicate references. It preserves typed observations, the whole
  quality/numeric policy, validity, truncation, fixed bindings and errors. Full is
  the compatibility default; `control/status` always retains full diagnostics.
  This presentation choice is excluded from mutation identity and never changes
  saved jobs. It is not a shorter set of correctness guarantees.
- `control/wait` can request a bounded observation, removing the extra read after
  mechanical waiting. The observation may still be partial; state and finality
  remain separate. No replay, cancellation or automatic resume behavior changes.
- Reads expose the committed revision's `row_count` (not total source rows).
  SQL checks reuse an included count/quality rather than inspecting the same
  result again. Old runtimes retain the client's metadata fallback.
- Metadata inspection supports case-insensitive column-name substring `search`,
  with the original schema index as the next offset. Searching never chooses a
  column for a scanning operation. Context inspection returns fixed bindings,
  schema and stored purpose/SQL/input definitions without scanning original data.
  Long definitions are omitted as a whole with an explicit marker; full scope
  inspection remains available. Caller-authored text is data, not instructions.
- CLI `--compact`, NDJSON requests, MCP `_request.response_mode` and the optional
  Python client use the same native projection. `rowtrail --compact mcp-config`
  configures that preference without modifying any host settings. No new MCP tool
  needs to be loaded. Metadata schema stays 9; no persisted option was added.

## Code composition and a reproducible first experience

`query(fetch=False)` saves a fixed result with zero observed rows. It is not an
execution limit. `context()` is one bounded metadata call, and `query()` can
consume the final wait observation. The complete contracts remain accessible.

`rowtrail demo` runs a bundled stdlib-only Python script, generates its own data in
a new private directory and retains transcripts, decision cards and reports. It
shows column discovery, saved intermediates, exact answers, a reusable assertion,
reconnection and result-package import in a separate process without originals.
It never makes model calls or downloads data/dependencies. Existing paths cannot
be overwritten. The optional demo needs Python; native data operations do not.

## Evidence boundaries

Measure full/compact on identical operations and row/byte budgets. Retain requests,
responses, raw repetitions, tokenizer encoding/version and binary hashes. Count
requests and responses separately, and distinguish transport bytes from the
smaller output of composed code. Tokenizer counts are not billed model usage.

The direct DuckDB baseline can keep its connection, intermediate tables and a
durable database. It can project/aggregate before returning rows. No comparison
may depend on printing an entire table or denying persistence to the baseline.
RowTrail's additional value is a standard contract for quality, versions, purpose,
checks and handoff, which a database application could also implement. Report the
extra latency/context for those guarantees. Scripted tests do not establish
better real-agent reasoning, adoption or universal token/engine-speed savings.

No new runtime/CLI dependency, schema migration, model SDK, natural-language
planner, automatic query retry or silent quality simplification is introduced.
