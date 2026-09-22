# Alpha.7 experiment inventory

These are development trials, not additional independent confirmations of the
final release. Final repeated runs are one directory above and identify final
binary hashes. Do not select the fastest earlier sample as the shipped result.

- `baseline-small` / `inline-small`: preliminary three-repeat file vs 64 KiB inline
  observations. The ~66 KiB saved subset remained a file; final threshold is 128 KiB.
- `inline-threshold-small`: **incomplete/failed trial**, retained with its status.
  A virtual inline path incorrectly reached filesystem canonicalization. Fixed
  by literal paths; final integration checks include inline reuse and special names.
- `ranges-million`: five repeats before/after exact range reads, only ~1% total
  difference in this layout. The wide/narrow-projection fixture demonstrates when
  avoiding inter-column gaps actually reduces bytes; it is not a universal gain.
- `before-shared-connection-*`: candidate timings before coordinator SQLite read
  connection reuse. The latency record also contains the startup-polling trial
  and a large startup outlier; no samples were discarded.
- `before-final-poll-restore-*`: later serial candidate, shared read connection,
  adaptive startup polling. Startup was inconsistent; final source restores the
  existing 10 ms polling. The final build also fixes worker-loss ACK classification.
- `partitions4-*`: five alternating trials. 1M exploration: 235.06 → 181.64 ms;
  16K: 29.45 → 30.03 ms. **Rejected default**: the 32 MiB sort fails with
  RESOURCE_EXHAUSTED during parallel sort-merge reservations. The error log is
  retained. Direct engines still use one thread/partition, so comparisons against
  this four-partition arm do not have an equal CPU budget.
- `paging-alpha6`, `paging-alpha7`, `paging-after-connection`: sequential CLI
  samples, including startup, varied (10K 8.78 / 8.87 / 9.73 ms). The final
  alternating persistent-session matrix removes that startup confound; it shows
  small-page improvement and essentially unchanged 10K pages. No general CLI
  paging speedup is claimed for alpha.7.

The early `before_ranges` prototype is used in the final projection matrix only
for an external-source query, to isolate range-read bytes. It predates the inline
path correction and is not a valid candidate for a saved-inline-result workflow.
All development variants use the same pinned dependency set. No failed prototype
is published as a release binary.
