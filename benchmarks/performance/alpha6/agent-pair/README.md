# Alpha.6 real external-agent pilot

All 12 planned trials passed; no selective trial retries. See the
[complete interpretation](../../../../docs/verification.md#real-external-agent-paired-pilot),
[full report](report.json) and [summary](summary.json). Raw reasoning stays local.
The requested model is GPT-6 Astra at xhigh; Codex CLI is 0.154.0-alpha.6.2.
A persistent Python process is provided to both arms; DuckDB's connection/tables
persist. The fixture and binary SHA-256 hashes are recorded in the report.

Setup/prompt snapshots:

- [Explore / RowTrail prompt](explore-rowtrail-prompt.txt), [usage](explore-rowtrail-usage.md)
- [Explore / DuckDB prompt](explore-duckdb-prompt.txt), [usage](explore-duckdb-usage.md)
- [Handoff / RowTrail prompt](handoff-rowtrail-prompt.txt), [usage](handoff-rowtrail-usage.md)
- [Handoff / DuckDB prompt](handoff-duckdb-prompt.txt), [usage](handoff-duckdb-usage.md)

The bootstrap and profiling changed since alpha.5. Do not treat model wall-time
differences across those pilots as a controlled A/B. Cumulative input includes
cached tokens and scaffolding; bridge bytes are not peak context or total tokens.
The shorter RowTrail setup did not improve both tasks, and neither task shows
an overall advantage over persistent DuckDB in this small synthetic pilot.
