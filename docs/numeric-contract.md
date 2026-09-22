# Numeric contract

RowTrail preserves SQL types and sends Int64, UInt64 and Decimal values as JSON
strings. The standard-library helper can decode them to Python `int`/`Decimal`
with `typed_rows()`; the original observation remains unchanged.

`accuracy: exact` means the computation is not a sampling estimate. Coverage,
completion, presentation truncation and numeric semantics are separate contracts.
Exact does **not** certify every SQL expression as overflow-free.

## Checked SQL SUM

New jobs declare `quality.numeric.policy: rowtrail-numeric-v1`. SUM over Int64,
UInt64 and Decimal32/64/128/256 uses checked i256 intermediate sums and checks the
final declared output range. This applies to ordinary, grouped, DISTINCT, partial
merge and sliding-window evaluation. Smaller integers keep the engine's coercion
to Int64/UInt64. Decimal precision increases by ten, capped by its storage type's
maximum; scale is preserved. Empty/all-null groups return NULL.

A partial sum may exceed Int64 and return to range after merging: it is carried
as wide state, not prematurely rejected or wrapped. The i256 intermediate and
UInt64 count remain finite; exceeding either fails explicitly. DISTINCT state is
charged to the engine's aggregate memory accounting, and can exhaust the budget.

For example, `SUM` of BIGINT values 9223372036854775807 and 1 fails with
`ARITHMETIC_OVERFLOW`. Casting **inputs** to `DECIMAL(38,0)` produces the exact sum
9223372036854775808. Casting the output of a narrow SUM is too late. A Decimal sum
of 38 nines plus one also fails: its declared precision cannot represent the sum.

The durable job error includes `code`, `message`, `retryable: false`, and details
with `operation`, `data_type`, `expression`, `reason`, and recovery guidance.
No invalid batch is committed. A previously published valid preview can remain
readable, but its quality is nonfinal and the job failed; `rows()` rejects it.

## Decimal boundary and remaining engine semantics

Decimal precision is checked before writing result parts, presenting observations
and exporting, including logically visible values nested in lists/structs/maps
and selected dictionary values. Null parent values and slices are respected.
This prevents an out-of-precision Decimal from becoming a misleading formatted
string. Reading/exporting an invalid old Decimal fails instead of repairing it.

Float SUM, AVG, other aggregates, scalar arithmetic and casts retain DataFusion
semantics. They are **not** covered by the checked-SUM guarantee. Floating-point
rounding and nonfinite values remain possible. An upstream expression may already
have wrapped before reaching SUM; casting after that expression cannot recover
its intended value. Interval/duration SUM requires an explicit cast to numeric
units. RowTrail is not an arbitrary-precision mathematical engine.

Progressive `analyze` retains its separate checked count/integer-unit sum and
Decimal128(38,6) average truncated toward zero. See [agent integration](agent-guide.md).

## Reuse and old workspaces

The current policy and each input's declared policy appear in quality/lineage.
`input_provenance: unknown` means an input or its ancestry lacks a policy; this
propagates through subsequent reuse and is not a claim that those inputs are wrong. Ordinary external sources can lack provenance.
Old frozen revisions are not rewritten or recertified during the schema-8 upgrade.
A missing numeric policy means unknown. Already-wrapped legacy integers cannot
be detected from their saved value alone; recompute from original inputs if needed.

`rt.rows(response, numeric_policy='rowtrail-numeric-v1')` additionally requires
that declared policy. It still does not certify other SQL arithmetic or upstream
input provenance. `rows()` without that option checks successful, valid, exact,
complete, final and untruncated output, as before. Always retain the schema and
quality alongside any extracted rows. Parquet export/reopening carries quality;
CSV sidecars are not automatically reimported.
