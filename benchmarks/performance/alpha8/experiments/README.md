# Alpha.8 retained experiments

All timing files retain raw repeats and binary hashes. These are candidate
measurements; the parent directory contains the final executable comparison.

- `parallel-auto-1m.json`: conservative automatic targets, before encoded-part
  coalescing; about 236 → 193 ms in five alternating complete workflows.
- `parallel-auto-small.json`: about 36.5 → 36.9 ms, essentially unchanged.
- `resources-auto.json`: the same 32 MiB million-row sort still passes; no blanket
  four-partition default and no reduced integrity/durability guarantee.
- `encoded-parts-1m.json`: actual IPC bytes guide coalescing, about 192 → 146 ms
  versus the parallel-only candidate. Compressible materialization needs fewer
  parts/commits, retaining the 8 MiB encoded and 128-batch limits.
- `encoded-parts-entropy.json`: about 95.5 → 98.6 ms, a regression in this sample.
- `encoded-parts-2m-rescan.json`: complete save/reuse about 362 → 203 ms; all
  result/scan/cache byte counters are retained.
- `encoded-parts-1m-pages.json`: a real negative case, roughly 0.63 → 1.94 ms for
  the first 100 ID rows of a large result. Whole-part checksumming reads more
  bytes after coalescing. The final report keeps this tradeoff visible.
- `resources-encoded-parts.json`: a single structural/resource trial, before the
  final alternating five-repeat sort series. Sampled RSS is not an RSS cap.

Final reporting reads the actual selected session target rather than recomputing
CPU availability after a job. Earlier completed series remain in Git history;
final release measurements are rerun with final executable hashes. None of these
backend trials establishes an overall model latency or token advantage.
