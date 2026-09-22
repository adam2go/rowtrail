"""Cold native startup and warm tiny-query latency, with raw repeated samples."""
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
p.add_argument('--warm-queries', type=int, default=100)
p.add_argument('--output', default='benchmarks/local/latency.json')
a = p.parse_args()
variants = {n: (ROOT / d).resolve() for n, d in (v.split('=', 1) for v in a.variant)}
records = []
with tempfile.TemporaryDirectory(prefix='rowtrail-latency-') as td:
    base = pathlib.Path(td)
    for repeat in range(a.repeats):
        names = list(variants)
        if repeat % 2:
            names.reverse()
        for name in names:
            binary = str(variants[name] / 'rowtrail')
            started = time.perf_counter()
            with RowTrail(binary, str(base / f'{name}-{repeat}')) as rt:
                doctor = rt.call('doctor', {})
                startup = (time.perf_counter() - started) * 1000
                latencies = []
                for i in range(a.warm_queries):
                    started = time.perf_counter()
                    result = rt.query(f'SELECT CAST({9007199254740993 + i} AS BIGINT) n')
                    assert rt.rows(result) == [[str(9007199254740993 + i)]]
                    latencies.append((time.perf_counter() - started) * 1000)
                records.append({'variant': name, 'repeat': repeat, 'startup_ms': startup,
                                'first_query_ms': latencies[0], 'warm_query_ms': latencies[1:]})
            os.kill(doctor['coordinator_pid'], signal.SIGTERM)
    def stats(values):
        values = sorted(values)
        return {'median': statistics.median(values), 'p95_nearest_rank': values[max(0, (95 * len(values) + 99) // 100 - 1)], 'min': values[0], 'max': values[-1], 'samples': len(values)}
    report = {'status': 'passed', 'kind': 'persistent native session; exact scalar oracle; no model calls',
              'platform': platform.platform(), 'repeats': a.repeats, 'warm_queries_per_session': a.warm_queries,
              'binary_sha256': {name: {n: hashlib.sha256((bins / n).read_bytes()).hexdigest() for n in ('rowtrail', 'rowtrail-runtime')} for name, bins in variants.items()},
              'records': records, 'summary': {name: {'startup_ms': stats([r['startup_ms'] for r in records if r['variant'] == name]),
                                                   'first_query_ms': stats([r['first_query_ms'] for r in records if r['variant'] == name]),
                                                   'warm_query_ms': stats([v for r in records if r['variant'] == name for v in r['warm_query_ms']])} for name in variants},
              'limitations': ['Fresh workspace/coordinator for each serial, alternating trial; OS caches not flushed.', 'Startup includes native CLI/coordinator and metadata initialization; first query also starts a worker.', 'Warm samples share a workspace and worker; p95 is observational, not a latency guarantee.']}
    out = ROOT / a.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['summary'], indent=2))
