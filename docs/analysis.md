# Save, compare, reuse and hand off an analysis

Beta.2 adds a native `snapshot` contract and optional analysis composition in the
stdlib Python client. There is no dataframe library, model, scheduler or external
database dependency. CLI, NDJSON, MCP (`data_snapshot`) and the Rust client share
the native snapshot contract. The higher-level helpers below currently require
Python; they are not additional native MCP tools.

```sh
rowtrail python-client > rowtrail_client.py
# Or, from the source tree / an extracted native archive:
python3 examples/analysis_quickstart.py --rowtrail "$PWD/rowtrail"
```

## Start from an existing project

Export your cleaned dataframe to Parquet using your existing engine. For example,
Pandas `frame.to_parquet('clean.parquet', index=False)`, Polars
`frame.write_parquet('clean.parquet')`, or DuckDB `COPY (...) TO 'clean.parquet'
(FORMAT PARQUET)`. Those engines stay optional and separate from RowTrail.

```python
from rowtrail_client import RowTrail

with RowTrail(workspace='.rowtrail') as rt:
    data = rt.from_parquet('clean.parquet', label='sessions', provenance={
        'origin': 'clean.py:42',
        'code': 'frame.to_parquet("clean.parquet", index=False)',
        'description': 'Filtered bot traffic; amounts in integer cents',
    })
    result = rt.query('SELECT channel, SUM(amount) total FROM t GROUP BY channel',
                      {'t': data}, provenance={'description': 'Revenue by channel'})
    rt.export(result, 'totals.parquet')
# Use your existing Pandas/Polars/DuckDB reader to plot or model totals.parquet.
```

`snapshot(source)` accepts an external dataset binding or a completed exact,
complete fixed result revision. It copies data into managed Parquet under native
scan, memory, result, spill, timeout and quota limits. No rows are collected in
Python. Only a completed copy publishes the dataset; it survives original file
changes. `prepare` retains its existing CSV/TSV-only interface.

The source/generating code/description are **caller declarations**. They are
stored, never executed. Description and origin have 4,096 UTF-8-byte limits; code
has 16,384 bytes. Labels retain their 256-byte limit. These fields cannot certify
business correctness or make unknown numeric provenance known.

For ambiguous transport recovery, use `open` and `snapshot` separately, retain
the open binding and snapshot idempotency key, and inspect durable state. The
convenience `from_parquet` is a composition, not an atomic cross-call transaction.

## Small answers and bounded records

`rt.scalar(response)` requires one exact, complete, final, untruncated row and
column. `rt.records(response)` returns named values from an already-included
complete observation, preserving integers and Decimal values. Neither paginates
or collects an entire table implicitly. Use explicit `pages` budgets or Parquet
export for larger data.

A tight query output budget prioritizes the observation over optional job metrics.
`control/status` retains the full metrics. If the full observation cannot fit,
the response says it was omitted and retains the fixed result/job references.

## Save an executable assumption

A check is a read-only SELECT whose returned rows are violations:

```python
unique = rt.check('SELECT customer_id, COUNT(*) n FROM t '
                  'GROUP BY customer_id HAVING COUNT(*) > 1', {'t': customers},
                  name='unique customer IDs', samples=10)
nonnull = rt.check('SELECT * FROM t WHERE customer_id IS NULL', {'t': customers})
no_growth = rt.check('SELECT (SELECT COUNT(*) FROM before_join) before_rows, '
                    '(SELECT COUNT(*) FROM after_join) after_rows '
                    'WHERE (SELECT COUNT(*) FROM after_join) > '
                    '(SELECT COUNT(*) FROM before_join)',
                    {'before_join': orders, 'after_join': joined})
```

The result includes `passed`, total `violations`, a fixed violation binding,
quality, bounded examples, scope and job metrics. It saves the complete violation
result under native limits; a large failing check can hit its result budget.
Partial coverage, nonfinal results, arithmetic/SQL/resource errors cannot produce
a pass. Callers decide what to do with a failure. Recipe checks stop subsequent
steps by default. For schema-only leakage checks, use `rt.check(bindings={'t': training},
forbidden_columns=['target', 'future_outcome'])`. This compares exact column names
against the complete schema and saves the violating names. A recipe check can
use `forbidden_columns` instead of SQL. SQL checks cover business relationships
and totals; there is no separate general constraint language.

## Compare two versions

```python
comparison = rt.diff(before, after, keys=['customer_id'], samples=10)
```

Both inputs must be final exact complete result revisions. Full native SQL scans
check uniqueness and null keys first; duplicate/null/missing/incompatible keys
return `status: blocked` with the reason. Successful comparison returns before /
after / added / deleted / modified counts, changed-field counts, schema changes
and bounded deterministic examples. Python does not load either full table.

Modified rows compare **common columns with identical declared types**. Added,
removed or type-changed columns are reported separately. The report lists the
comparison columns; it does not pretend incompatible fields were compared.
A Decimal expression may widen its declared precision; cast both compared
projections to the intended common type if appropriate. SQL equality semantics
apply (including float/NaN/nested values); no tolerance
mode or string-to-number coercion is implied. Up to 16 keys, 128 comparable
non-key columns and 100 examples are accepted. Engine scan/memory/time budgets
apply to each constituent query. This first version performs several scans;
it does not claim a single-pass diff or a cumulative cross-query engine budget.

## Re-run an analysis with new input

Recipes are versioned JSON, not executable Python:

```python
recipe = {
    'format': 'rowtrail.recipe.v1', 'inputs': ['orders'],
    'parameters': {'minimum': {'type': 'Int64', 'value': '100'}},
    'steps': [
        {'id': 'selected', 'bindings': {'t': 'input:orders'},
         'sql': 'SELECT * FROM t WHERE amount >= $1', 'parameters': ['minimum']},
        {'id': 'unique_ids', 'kind': 'check', 'bindings': {'t': 'step:selected'},
         'sql': 'SELECT id FROM t GROUP BY id HAVING COUNT(*) > 1'},
        {'id': 'total', 'bindings': {'t': 'step:selected'},
         'sql': 'SELECT SUM(amount) total FROM t'},
    ],
}
run = rt.run_recipe(recipe, {'orders': next_week}, run_dir='runs/week-2',
                    parameters={'minimum': {'type': 'Int64', 'value': '150'}})
```

Each run creates a new directory exclusively. `run.json` records the recipe,
parameters, fixed inputs and identity, per-step idempotency key, accepted job,
result binding and check outcome. Metadata updates use file and directory fsync.
A failed step preserves earlier successes; transport failure is never replayed.
Exceptions expose `run_record` and `run_path`. `rt.lookup(key)` (native
`control` action `lookup`) retrieves committed acceptance and job state without
replaying SQL. A missing key is only a point-in-time observation; an in-flight
request may still commit later. `stop_on_failure=False` lets a caller
continue after a completed failing assertion, but execution errors still stop.

Dependencies must refer to named inputs or preceding steps. Limits: 64 inputs,
64 steps, 1 MiB recipe, 8 MiB run metadata. Scheduling can use cron or CI. There is
no automatic retry, interrupted-run resume, parallel DAG scheduler or whole-run
transaction. Accepted jobs retain the normal RowTrail durability contract.

## Export and continue a branch

```python
rt.pack(result, 'handoff', notes='Revenue after excluding bot traffic')
# Add include_inputs=True to ship input payloads too, when appropriate.
verified = RowTrail.verify_package('handoff')  # offline, no native process needed
with RowTrail(workspace='recipient-workspace') as recipient:
    imported = recipient.import_package('handoff')
    followup = recipient.query('SELECT SUM(total) FROM t', {'t': imported})
```

The new directory contains a versioned `manifest.json`, a readable `report.md`,
Parquet results and export sidecars. Reports include bounded five-row previews
with their own truncation indicators; Parquet payloads remain complete. It records the result DAG, executed SQL,
parameters, source identities and schemas, quality, fixed revisions and notes.
Managed snapshots are explicit independence boundaries; their generating scopes
are provenance, not live dependencies. Original local paths are included in
identity metadata: review the report/manifest before sharing sensitive details.

A package is complete only when `manifest.json` has been published. Interrupted
packaging retains `failure.json` and completed jobs; it cannot be imported as a
complete package. Destinations are never overwritten. Share the directory or
compress it with your existing tools; RowTrail does not extract archives.

Verification has deliberate limits:

- `verified_bytes` means recorded sizes and SHA-256 match included files. It does
  not prove authorship, business correctness, source freshness or SQL reproduction.
- Result-only packages support inspection and follow-ups. Missing inputs are
  listed explicitly. Import never follows original paths or executes saved SQL.
- Import copies verified payloads through a private bounded streaming file into
  native managed snapshots, then checks schemas and row counts. It returns a
  mapping and the original manifest. Completed imports remain if a later node
  fails; the exception retains `imported_bindings`.
- The returned `recipe` is inert. With explicit input mappings (or included input
  snapshots), call `run_recipe` to recompute. Only then has SQL been rerun.

Defaults: at most 64 nodes, 1 GiB included files, 8 MiB manifest/report. File names
must be simple regular files; symlinks, traversal, duplicates, missing nodes and
cycles are rejected. The manifest is a declaration, not a signed attestation.
No automatic retention cleanup or entire-package transaction is claimed.

## Source validity and independence

`inspect(..., checks=('provenance',))` distinguishes stored validity from actual
verification. Inspect does not scan files. Reading/querying saved results verifies
the parts used; it does not recheck every original ancestor. Using an original
source can discover a change and invalidate its dependents. This remains the
existing contract. Create an independent `snapshot` **before** invalidation when
you want to keep a standalone version. Already-invalid or partial results cannot
be promoted through snapshot. Old numeric provenance remains unknown when unknown.
