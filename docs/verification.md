# Verification: beta.1

[Home](../README.md) · [Numeric contract](numeric-contract.md) · [Current limits](progress.md)

Beta.1 is undergoing final native verification. Local acceptance already covers
112 integration scenarios and 14 Rust tests, published-format schema-7 to schema-8
upgrades, invalid legacy Decimal rejection and a 32 MiB out-of-core sort whose
exported rows all match an independent oracle. Native artifacts are not published
until both build platforms, package limits, installation and checksums pass.

The work started as alpha.9. Its [initial three-repeat experiment](../benchmarks/performance/beta1/experiments/alpha9-followup-initial.json)
saves a 2M-row subset, runs ten queries, reconnects and discovers the fixed result.
On one Mac, median complete elapsed time was 916.46 → 726.25 ms; ten follow-ups
568.16 → 380.75 ms. Each follow-up read about 82.03 → 28.16 MB of saved data and
zero original-source bytes. Save time rose slightly and response bytes increased.
These are initial candidate measurements, not the final release benchmark.

## Native distribution

Final native provenance and repeated release-binary measurements will replace
this pending status after acceptance. Archive/CLI/runtime budgets remain
30 / 4.5 / 125 MB. The runtime and stdlib helper add no external dependency.

[Alpha.8 evidence](releases/alpha8-verification.md) and
[artifact provenance](releases/alpha8-verification.json) remain archived.
