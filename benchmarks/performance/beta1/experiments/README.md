# Pre-release experiments

- `alpha9-followup-initial.json`: three alternating trials before final beta
  versioning/instrumentation; retain the cold first-trial outlier. Same 2M-row
  source and independent oracle; candidate saved-result scans keep whole parts.
- `legacy-provenance-before.json`: a later regression extends legacy reuse across
  three generations and fails against the previous candidate. Checking only an
  immediate input's policy lost unknown ancestry after repeated derivation. The
  fix also requires declared input ancestry before reporting it as declared.
  This failed candidate was not published; final suites rerun the extended test.
