"""Bounded-memory workloads that outlive control-message scheduling jitter."""
# Expand each narrow input to 65,536 rows. The cross product has >4 billion
# candidates but retains only narrow build-side rows and one count accumulator.
# A nonseparable modulo predicate prevents a metadata-only product count.
LONG_QUERY = '''WITH expanded AS (
SELECT id FROM t UNION ALL SELECT id FROM t
UNION ALL SELECT id FROM t UNION ALL SELECT id FROM t
)
SELECT COUNT(*) FROM expanded a CROSS JOIN expanded b
WHERE ((a.id % 1009) * (b.id % 1013)) % 97 = 3'''
