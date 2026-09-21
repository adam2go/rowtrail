# Retained alpha.6 experiments

All timing files include binary hashes and repeated raw records. The exploratory
million-row series ran each candidate separately; machine/cache drift affects its
absolute timings. The final [alternating exploration matrix](../exploration-1048576.json)
is the release comparison. Preliminary binary versions still identify as alpha.5.

| File | Candidate | Five-query median ms | Saved bytes |
|---|---|---:|---:|
| baseline-million.json | alpha.5, plain IPC, 4 MiB target | 354.61 | 33,517,954 |
| buffered-million.json | 64 KiB writer buffer only | 332.87 | 33,517,954 |
| zstd-million.json | buffer + IPC Zstd level -3, 4 MiB | 322.34 | 9,931,906 |
| lz4-million.json | buffer + IPC LZ4, 4 MiB | 325.51 | see raw metrics |
| zstd1-page-million.json | Zstd level 1 + page accounting, 4 MiB | 321.88 | 4,219,778 |
| coalesced-million.json | large-only Zstd level 1 + pages, 6 MiB | 293.54 | 4,217,972 |

Buffering alone was not isolated from machine drift well enough to establish a
specific speedup. Zstd level 1 saved more bytes than LZ4/-3 with comparable whole
workflow time. The final combination also reduces part/commit count. File sync,
directory sync and SQLite FULL durability stayed enabled throughout.

`entropy.json` interleaves four candidates on random integer columns. Medians:
plain 148.61 ms, Zstd level 1 + pages 148.19 ms, LZ4 151.96 ms, Zstd -3 147.97 ms.
Compression alone is not a general speed improvement. `coalesced-entropy.json`
adds the larger target (alpha.5 148.31 ms, candidate 139.27 ms).
`*-paging.json` retain the initial page comparisons; final versioned binaries
were measured again in the parent directory.

Fixture setup initially exposed DuckDB COPY parameter binding order; the harness
now names source/destination parameters explicitly. The first high-entropy oracle
also caught SQL Int64 accumulator overflow: the workload now explicitly sums
Decimal(38,0) inputs and checks Python's arbitrary-precision totals. These failed
setup/oracle attempts produced no accepted timing records. A new integration test
initially shadowed its expected row variable; this test bug was fixed before the
full passing run. Raw development traces remain local because they contain paths.
