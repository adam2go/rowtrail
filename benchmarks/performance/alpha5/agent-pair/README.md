# Real agent paired pilot

12 planned trials, all retained. See [the report](../../../../docs/verification.md#real-external-agent-paired-pilot) for interpretation.

- `report.json`: independent expected/actual answers, full sanitized backend/code traces, non-reasoning CLI events, metrics and summary.
- `summary.json`: the same setup, conditions and aggregate measurements without traces.
- `*-prompt.txt` and `*-usage.md`: actual task prompts and backend instructions supplied in these trials. The Python executable path is replaced with a repo placeholder; use your benchmark virtual environment.

Both arms can retain Python variables, execute composed code and materialize tables.
The experiment is deliberately small and does not show a RowTrail efficiency win.
Raw model reasoning events stay local and are excluded from these records.
The 128 MiB engine setting is not a process RSS cap.
