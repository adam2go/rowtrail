# Third-party notices

`inventory.json` records the full Cargo lockfile dependency graph, including
platform-specific dependencies that are not linked in every binary. Each package
retains its upstream license declaration and notice files in `licenses/`.

Some crates omit their repository-level license file from the published archive.
`supplemental-notices.json` identifies the exact upstream revisions used to supply
those notices. `scripts/licenses.py` refreshes the inventory from Cargo metadata
and preserves these supplemental files. For dual/triple-licensed packages, the
permissive licensing option is used; all supplied upstream notices are retained.

These notices cover dependencies; RowTrail's own license is the root Apache-2.0
`LICENSE`. The product does not link the DuckDB benchmark dependency.
