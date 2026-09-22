# Pre-release experiments

- `alpha9-followup-initial.json`: three alternating trials before final beta
  versioning/instrumentation; retain the cold first-trial outlier. Same 2M-row
  source and independent oracle; candidate saved-result scans keep whole parts.
- `legacy-provenance-before.json`: a later regression extends legacy reuse across
  three generations and fails against the previous candidate. Checking only an
  immediate input's policy lost unknown ancestry after repeated derivation. The
  fix also requires declared input ancestry before reporting it as declared.
  This failed candidate was not published; final suites rerun the extended test.

- `before-startup-fix/`: the first complete beta matrix. Ten-query reuse improves,
  but an unnecessary fsync on the ephemeral endpoint descriptor adds severe cold
  startup latency in the later scalar series. Scalar numeric aggregation also
  pays i256 per-row overhead. These results are retained, not cherry-picked away.
  Subsequent candidates remove only descriptor fsync (job/result durability is
  unchanged) and use checked i128 batch folds with a checked i256 fallback.

- `latency-without-endpoint-fsync.json`: workspace-startup median returns to
  about 18 ms after removing only the ephemeral descriptor barrier. A first
  candidate invocation takes 2.12 seconds; it remains in this record. These are
  fresh-workspace measurements, not guarantees for a never-executed binary.
- `entropy-single-lane.json` and `checked-sum-lanes.json`: checked i128 batch
  accumulation reduces per-row i256 work; four independent checked lanes for
  non-null inputs remove most of the remaining high-entropy overhead. All lane
  sums merge in checked i256; i128 overflow flushes rather than wraps or fails
  prematurely. Seven alternating trials compare both variants and alpha.8.
