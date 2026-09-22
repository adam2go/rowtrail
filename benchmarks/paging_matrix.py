"""Alternate bounded page reads on persistent sessions and check every ID."""
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
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples'))
from session_client import RowTrail
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--variant', action='append', required=True)
p.add_argument('--repeats', type=int, default=21)
p.add_argument('--source-rows', type=int, default=16384)
p.add_argument('--wide-result', action='store_true', help='Save id/region/amount; pages still project only id')
p.add_argument('--output', default='benchmarks/local/paging-matrix.json')
a = p.parse_args()
variants = {n: (ROOT / d).resolve() for n, d in (v.split('=', 1) for v in a.variant)}
records, clients, refs, pids = [], {}, {}, {}
with tempfile.TemporaryDirectory(prefix='rowtrail-pages-') as td:
    base = pathlib.Path(td)
    try:
        assert a.source_rows >= 10000
        subprocess.run([str(next(iter(variants.values())) / 'rowtrail-runtime'), 'fixtures', '--directory', str(base / 'data'), '--rows', str(a.source_rows)], check=True, stdout=subprocess.DEVNULL)
        for name, bins in variants.items():
            rt = clients[name] = RowTrail(str(bins / 'rowtrail'), str(base / name))
            pids[name] = rt.call('doctor', {})['coordinator_pid']
            columns = 'id,region,amount' if a.wide_result else 'id'
            refs[name] = rt.binding(rt.query(f'SELECT {columns} FROM t ORDER BY id', {'t': rt.open(base / 'data/many.parquet')}))
        for repeat in range(a.repeats):
            names = list(variants)
            if repeat % 2:
                names.reverse()
            for rows in (100, 1000, 10000):
                for name in names:
                    start = time.perf_counter()
                    result = clients[name].call('read', {**refs[name], 'columns':['id'], 'max_rows': rows, 'max_bytes': 1000000})
                    elapsed = (time.perf_counter() - start) * 1000
                    assert result['rows'] == [[str(9007199254740993 + i)] for i in range(rows)]
                    records.append({'variant': name, 'repeat': repeat, 'rows': rows, 'ms': elapsed, 'read_metrics': result['read_metrics']})
        def summary(values):
            values = sorted(values)
            return {'median_ms': statistics.median(values), 'p95_ms': values[(95 * len(values) + 99) // 100 - 1]}
        report = {'status': 'passed', 'kind': 'alternating bounded page reads; persistent sessions; no model calls',
                  'platform': platform.platform(), 'repeats': a.repeats, 'source_rows':a.source_rows, 'materialized_columns':columns,
                  'binary_sha256': {name: {n: hashlib.sha256((bins / n).read_bytes()).hexdigest() for n in ('rowtrail', 'rowtrail-runtime')} for name, bins in variants.items()},
                  'records': records, 'summary': {name: {str(rows): summary([r['ms'] for r in records if r['variant'] == name and r['rows'] == rows]) for rows in (100, 1000, 10000)} for name in variants},
                  'limitations': ['Same fixed sorted IDs; separate persistent workspaces. Each timing includes response transfer and JSON parsing, not startup/materialization.', 'OS cache not flushed. All repeats retained, including first reads. Native CLI startup is measured separately.']}
        out = ROOT / a.output; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report['summary'], indent=2))
    finally:
        for name, rt in clients.items():
            rt.__exit__()
            if name in pids:
                os.kill(pids[name], signal.SIGTERM)
