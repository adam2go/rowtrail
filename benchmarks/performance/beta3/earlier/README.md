# Earlier complete measurements

These are the first full benchmark series, before the final MCP invalid-preference
classification and Python observation-error projection fixes. Both fixes concern
error paths. All four benchmarks were repeated on the final local binaries; those
reports live one directory up. Keep this series so outliers and less favorable
measurements remain visible; there was no selective trial removal.

Initial response medians: 4,706 full / 3,461 compact o200k_base tokens (26.5% fewer).
The sum of request/response medians fell 20.9%. Initial 2M-row follow-up medians
were 683.78 / 689.54 ms, and 1M-row exploration 144.62 / 146.23 ms (beta.2 / beta.3).
The beta.2 cold-start samples include a 2,276 ms outlier. Binary hashes
are retained in the raw files; generated references affect tokenizer counts.
