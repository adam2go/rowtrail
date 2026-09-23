# 010 — Portable analysis with explicit execution boundaries

Status: accepted and verified for 0.1.0-beta.2.

The beta.1 external review asks for incremental adoption in existing Python
projects, shareable branches, reusable recipes, revision comparison and executable
business assumptions. These form one workflow, using the existing native engine.

## Native foundation

`snapshot` streams a dataset (including Parquet) or a final exact complete result
into managed Parquet. It uses prepare's quota, worker, checksum, cancellation and
atomic publication path. A successful copy is independent of the external source;
failed copies expose no dataset. `prepare` retains its CSV/TSV contract.
Caller-written provenance (origin, description, code) is bounded text, never
executed. Query scopes retain it; managed datasets retain it with their label.
Schema 9 prevents old runtimes ignoring these new persisted options.

`inspect` distinguishes stored validity from actual verification and can expose
source identity/provenance explicitly. Existing revisions are not recertified.
Ordinary dependent results keep beta.1 invalidation behavior: querying a frozen
result checks that result's parts, not the freshness of every original ancestor.
A source operation discovering a change invalidates dependent results. Use an
explicit independent snapshot before that happens to retain a standalone copy.

A small answer takes priority over optional job metrics in bounded responses.
Quality, fixed revision and truncation remain visible; a missing observation is
never a complete answer.

## Optional Python composition (stdlib only)

The printed client adds snapshot/import, scalar/named bounded observation access,
SQL assertions, keyed diff, recipes and branch packages. There is no internal
model, dataframe engine, scheduler or added runtime dependency. CLI/MCP expose the
underlying native contracts; higher-level composition is initially Python only.

- Checks are SELECT queries whose returned rows are violations. They save the
  complete violation result within native resource limits, return a count and
  bounded examples, and cannot pass on partial/nonfinal data or execution failure.
- Diffs compare complete fixed revisions with exact key uniqueness/non-null
  preconditions. Counts cover all rows, samples are bounded; schema differences
  and incompatible keys are explicit. SQL does the join, never Python row loading.
- Recipes are versioned JSON with named inputs, typed parameter references, ordered
  dependency steps and checks. Each explicit run creates its own durable record;
  per-step idempotency keys are written before submission. Errors and partial runs
  remain inspectable. Imported code/SQL is never executed automatically.
- Packages are directories with a versioned manifest, Markdown report and streamed
  Parquet results. Dependencies and original identities/SQL/parameters/notes are
  retained. Inputs are optionally included. File digests detect changed bytes,
  not authenticity or correctness of authored SQL/notes. Import validates bounded
  files and copies them into independent snapshots, with an explicit mapping.
  Original files are never silently read or restored by an import. A result-only
  package supports result verification and follow-ups; original recomputation
  needs explicit input mapping and an explicit recipe run.

Limits are public and enforced: metadata, node/step counts, payload bytes, samples,
and engine budgets. Package/recipe operations may leave completed native jobs on
failure; they report their IDs and do not claim cross-operation transactions.
Native data retention and dependency-safe GC remain authoritative.

The assertion and versioned artifact designs borrow the general pattern from
[dbt data tests](https://docs.getdbt.com/docs/build/data-tests) and
[dbt artifacts](https://docs.getdbt.com/reference/artifacts/dbt-artifacts), without
adding dbt or claiming its feature coverage. Adoption benefits remain hypotheses.

## Release evidence

Add real chain tests, malformed/tampered/partial cases, source deletion, duplicate
and null keys, recipe failure/replacement inputs, a package round trip on a fresh
workspace and independent correctness oracles. Preserve beta.1 binaries, repeat
complete-task timings, and enforce the existing native size/dependency ceilings.
