"""One long-lived workspace: retain every durable result and observe history cost."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import statistics
import sys
import tempfile
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples'))
from session_client import RowTrail
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--bin-dir', default='target/release')
p.add_argument('--queries', type=int, default=5000)
p.add_argument('--window', type=int, default=100)
p.add_argument('--output', default='benchmarks/local/history.json')
a = p.parse_args()
assert a.queries >= 2 * a.window and a.window > 0
bins = (ROOT / a.bin_dir).resolve()
with tempfile.TemporaryDirectory(prefix='rowtrail-history-') as td:
    workspace = Path(td) / 'workspace'; pid = None
    try:
        samples = []
        with RowTrail(str(bins / 'rowtrail'), str(workspace)) as rt:
            pid = rt.call('doctor', {})['coordinator_pid']
            for i in range(a.queries):
                start = time.perf_counter()
                response = rt.query(f'SELECT CAST({9007199254740993 + i} AS BIGINT) AS n')
                samples.append((time.perf_counter() - start) * 1000)
                assert rt.rows(response) == [[str(9007199254740993 + i)]]
            summary = rt.call('workspace', {'action':'summary','kind':'result','limit':1,'max_bytes':8192})
            assert summary['counts']['readable_results'] == a.queries
        def stats(values):
            values = sorted(values)
            return {'median_ms': statistics.median(values), 'p95_ms':values[(95 * len(values) + 99) // 100 - 1]}
        report = {'status':'passed','kind':'one retained workspace; no GC, no model calls','queries':a.queries,'platform':platform.platform(),'window':a.window,
                  'binary_sha256':{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ('rowtrail','rowtrail-runtime')},
                  'first_query_ms':samples[0],'early_warm_window':stats(samples[1:a.window+1]),'last_window':stats(samples[-a.window:]),
                  'all_query_ms':samples,'workspace_files_bytes_at_end':sum(f.stat().st_size for f in workspace.rglob('*') if f.is_file()),
                  'limitations':['One serial session, not a repeated or interleaved version comparison.','All results retained; early/late windows expose history growth, not cold/warm engine differences.','Workspace file bytes are logical file lengths including SQLite/WAL, not filesystem allocated blocks or product quota bytes.']}
        out=ROOT/a.output;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k not in ('all_query_ms','binary_sha256')},indent=2))
    finally:
        if pid:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
