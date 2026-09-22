"""Read two distant columns of a wide Parquet file; exact Python oracle."""
import argparse
import csv
import hashlib
import json
import os
import pathlib
import platform
import random
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import duckdb
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples'))
from session_client import RowTrail
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--variant', action='append', required=True)
p.add_argument('--rows', type=int, default=65536)
p.add_argument('--repeats', type=int, default=7)
p.add_argument('--output', default='benchmarks/local/projection.json')
a = p.parse_args()
variants = {n: (ROOT / d).resolve() for n, d in (v.split('=', 1) for v in a.variant)}
records = []
with tempfile.TemporaryDirectory(prefix='rowtrail-projection-') as td:
    base = pathlib.Path(td)
    rng, totals, maximum, minimum = random.Random(7102), [0, 0], 0, 2**63
    source = base / 'wide.parquet'
    with (base / 'wide.csv').open('w') as f:
        w = csv.writer(f)
        w.writerow([f'c{i}' for i in range(32)])
        for _ in range(a.rows):
            row = [rng.getrandbits(63) for _ in range(32)]
            totals[0] += row[0]; totals[1] += row[-1]
            maximum = max(maximum, row[0]); minimum = min(minimum, row[-1])
            w.writerow(row)
    con = duckdb.connect()
    columns = {f'c{i}': 'BIGINT' for i in range(32)}
    con.read_csv(str(base / 'wide.csv'), header=True, columns=columns).create_view('fixture')
    con.execute("COPY fixture TO ? (FORMAT PARQUET, COMPRESSION UNCOMPRESSED, ROW_GROUP_SIZE 2048)", [str(source)])
    con.close()
    expected = [[str(v) for v in [*totals, maximum, minimum]]]
    sql = 'SELECT SUM(CAST(c0 AS DECIMAL(38,0))),SUM(CAST(c31 AS DECIMAL(38,0))),MAX(c0),MIN(c31) FROM t'
    for repeat in range(a.repeats):
        names = list(variants)
        if repeat % 2:
            names.reverse()
        for name in names:
            bins = variants[name]
            started = time.perf_counter()
            with RowTrail(str(bins / 'rowtrail'), str(base / f'{name}-{repeat}')) as rt:
                opened = rt.open(source)
                open_ms = (time.perf_counter() - started) * 1000
                t = time.perf_counter()
                result = rt.query(sql, {'t': opened})
                query_ms = (time.perf_counter() - t) * 1000
                assert rt.rows(result) == expected, result
                records.append({'variant': name, 'repeat': repeat, 'total_ms': (time.perf_counter() - started) * 1000,
                                'open_ms': open_ms, 'query_ms': query_ms, 'metrics': result['job']['metrics']})
                pid = rt.call('doctor', {})['coordinator_pid']
            os.kill(pid, signal.SIGTERM)
    report = {'status': 'passed', 'kind': 'wide Parquet, narrow projection, separated column ranges; no model calls',
              'rows': a.rows, 'columns': 32, 'row_group_size': 2048, 'repeats': a.repeats, 'platform': platform.platform(),
              'fixture_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'fixture_bytes': source.stat().st_size,
              'binary_sha256': {name: {n: hashlib.sha256((bins / n).read_bytes()).hexdigest() for n in ('rowtrail', 'rowtrail-runtime')} for name, bins in variants.items()},
              'sql': sql, 'expected': expected, 'records': records,
              'summary': {name: {'total_ms': statistics.median(r['total_ms'] for r in records if r['variant'] == name),
                                  'query_ms': statistics.median(r['query_ms'] for r in records if r['variant'] == name),
                                  'source_read_bytes': statistics.median(r['metrics']['io']['source_read_bytes'] for r in records if r['variant'] == name)} for name in variants},
              'limitations': ['A deliberately wide, small-row-group layout exposes coalesced gap reads; gains depend on layout.',
                              'OS cache not flushed; serial alternating order, same frozen bytes and fresh workspaces.',
                              'Encoded read bytes include query-time footer reads, not open-time metadata or physical device I/O.']}
    text = json.dumps(report, indent=2).replace(str(base), '<fixture>').replace(str(base).lstrip('/'), '<fixture>') + '\n'
    out = ROOT / a.output; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(text)
    print(json.dumps(report['summary'], indent=2))
