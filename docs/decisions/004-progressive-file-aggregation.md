# Alpha.4: exact cumulative checkpoints over Parquet files

Status: implemented; native release checks are tracked in [release verification](../verification.md).

## Deliberately restricted contract

`analyze` accepts a frozen **Parquet dataset/manifest**, 1–16 aggregate descriptions,
and normal execution budgets. It supports count(*), count(column), sum(column)
and avg(column). There are no filters, grouping, expressions, arbitrary SQL,
sampling, estimates, row-group scheduling or continuation of an interrupted run.
The fragment is a complete file in frozen manifest order. A single-file Parquet
input has one fragment, even if it has many row groups.

Each file is streamed through the same restricted/counting engine store and memory
pool as queries. Only requested columns are projected. Accumulators are constant
in input size; the result store retains one small immutable checkpoint per completed
file when previews are enabled. `preview:none` writes only the final checkpoint.
Source identities are validated before/after fragments and after complete execution.
Managed-file checksums and physical byte reservations remain in effect.

## Types and arithmetic

Count returns UInt64. Sum and average accept Int64, UInt64 or Decimal128 with scale
0–6; float input is rejected explicitly. Signed checked i128 arithmetic accumulates
exact integer units. Sum returns Decimal128(38,input_scale). Average divides the
exact sum by the non-null count and returns Decimal128(38,6), truncating toward
zero at six fractional digits. Empty/all-null inputs have null sum/average and a
zero non-null count. Overflow fails explicitly with `ARITHMETIC_OVERFLOW`; a prior
checkpoint stays readable. This is a declared fixed-precision mean, not a floating
approximation or a population estimate.

An agent that requires an exact rational mean can request sum and count together.
It must not infer more than six fractional digits from `avg`.

## Replacement snapshots, not appended aggregate rows

A checkpoint revision selects exactly one sealed Arrow part. Earlier checkpoints
remain fixed and reusable after later files finish. `coverage.input_coverage`
records completed_files, total_files, the file unit and frozen order. Before terminal
completion, coverage remains partial and `final_for_request` is false, even when
all file checkpoints have just been written. The terminal transaction publishes a
new final revision; inherited partial input quality is still preserved.

The agent can query a fixed checkpoint through an ordinary ResultBinding, or
explicitly allow a non-final export. Subsequent work on that checkpoint inherits
partial coverage. A count of files is not a percentage of rows or bytes processed.
Cancellation, budget exhaustion and worker loss stop execution and preserve the
last committed full-file checkpoint. They do not save resumable accumulator state.

## Metadata compatibility

Store schema 4 adds a checkpoint-to-part map. Existing schema-3 prefix revisions
remain valid and are upgraded one-way when a new coordinator opens the workspace.
The version marker changes before any new-style result is written, so an older
runtime cannot open it and mistake checkpoint rows for an appended table.
Back up valuable preview workspaces before upgrading; downgrading the store is
unsupported. A still-running old coordinator retains its own capabilities until
its current jobs finish and it exits. Use a new workspace for immediate isolation.
