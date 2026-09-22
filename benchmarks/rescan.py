"""Repeated saved-result scans within one job; independent integer/Decimal oracle."""
import argparse
import hashlib
import json
import os
import pathlib
import platform
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples'))
from session_client import RowTrail

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--variant', action='append', required=True)
p.add_argument('--rows', type=int, default=1048576)
p.add_argument('--repeats', type=int, default=7)
p.add_argument('--output', default='benchmarks/local/rescan.json')
a = p.parse_args()
variants = {name: (ROOT / path).resolve() for name, path in (v.split('=', 1) for v in a.variant)}
records = []
expected = []
for name, predicate in [('missing', lambda v: v is None), ('negative', lambda v: v is not None and v < 0), ('nonnegative', lambda v: v is not None and v >= 0)]:
    ids = [9007199254740993 + i for i in range(a.rows) if predicate(None if i % 17 == 0 else i % 2001 - 1000)]
    expected.append([name, str(len(ids)), str(sum(ids))])
sql = """SELECT 'missing' kind, COUNT(*) n, SUM(CAST(id AS DECIMAL(38,0))) ids FROM saved WHERE amount IS NULL
UNION ALL SELECT 'negative', COUNT(*), SUM(CAST(id AS DECIMAL(38,0))) FROM saved WHERE amount < 0
UNION ALL SELECT 'nonnegative', COUNT(*), SUM(CAST(id AS DECIMAL(38,0))) FROM saved WHERE amount >= 0
ORDER BY kind"""
with tempfile.TemporaryDirectory(prefix='rowtrail-rescan-') as td:
    directory = pathlib.Path(td)
    first = next(iter(variants.values()))
    subprocess.run([str(first / 'rowtrail-runtime'), 'fixtures', '--directory', str(directory / 'data'), '--rows', str(a.rows)], check=True, stdout=subprocess.DEVNULL)
    source = directory / 'data/many.parquet'
    for repeat in range(a.repeats):
        names = list(variants)
        if repeat % 2:
            names.reverse()
        for name in names:
            bins = variants[name]
            started = time.perf_counter()
            with RowTrail(str(bins / 'rowtrail'), str(directory / f'{repeat}-{name}')) as rt:
                opened = rt.open(source)
                saved = rt.query('SELECT id,region,amount FROM t', {'t': opened})
                materialize_ms = (time.perf_counter() - started) * 1000
                start = time.perf_counter()
                result = rt.query(sql, {'saved': saved})
                rescan_ms = (time.perf_counter() - start) * 1000
                assert rt.rows(result) == expected, result
                assert result['job']['metrics']['io']['source_read_bytes'] == 0
                records.append({'variant': name, 'repeat': repeat, 'total_ms': (time.perf_counter() - started) * 1000,
                                'open_and_materialize_ms': materialize_ms, 'rescan_ms': rescan_ms,
                                'stored_bytes': saved['job']['metrics']['result_write_bytes'],
                                'result_parts': saved['job']['metrics']['result_parts'], 'metrics': result['job']['metrics']})
                pid = rt.call('doctor', {})['coordinator_pid']
            os.kill(pid, signal.SIGTERM)
    report = {'status': 'passed', 'kind': 'three logical scans of a saved result in one exact SQL job; no model calls',
              'rows': a.rows, 'repeats': a.repeats, 'machine': platform.platform(),
              'fixture_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'binary_sha256': {name: {n: hashlib.sha256((bins / n).read_bytes()).hexdigest() for n in ('rowtrail', 'rowtrail-runtime')} for name, bins in variants.items()},
              'sql': sql, 'expected': expected, 'records': records,
              'summary': {name: {key: statistics.median(r[key] for r in records if r['variant'] == name) for key in ('total_ms', 'rescan_ms')} for name in variants},
              'limitations': ['OS cache not flushed; serial alternating order, fresh workspace/session per trial.',
                              'Caches are job-local and byte-bounded; this does not measure cross-job caching.',
                              'A hand-fused conditional aggregation could avoid these logical rescans; this fixture measures reuse of a repeated scan plan, not optimal SQL.']}
    text = json.dumps(report, indent=2).replace(str(directory), '<fixture>').replace(str(directory).lstrip('/'), '<fixture>') + '\n'
    out = ROOT / a.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(json.dumps(report['summary'], indent=2))
