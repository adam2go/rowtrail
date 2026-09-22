"""Retain labeled history, restart, and measure indexed metadata-only handoff."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'examples'))
from session_client import RowTrail
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--bin-dir',default='target/release')
p.add_argument('--queries',type=int,default=5000)
p.add_argument('--lookups',type=int,default=31)
p.add_argument('--output',default='benchmarks/local/catalog.json')
a = p.parse_args()
assert a.queries >= 200 and a.lookups > 0
bins = (ROOT/a.bin_dir).resolve()
def stats(values):
    values=sorted(values)
    return {'median_ms':statistics.median(values),'p95_ms':values[(95*len(values)+99)//100-1]}
with tempfile.TemporaryDirectory(prefix='rowtrail-catalog-') as td:
    workspace=Path(td)/'workspace';pid=None;samples=[];lookups=[]
    try:
        with RowTrail(str(bins/'rowtrail'),str(workspace)) as rt:
            pid=rt.call('doctor',{})['coordinator_pid']
            for i in range(a.queries):
                started=time.perf_counter()
                answer=rt.query(f'SELECT CAST({9007199254740993+i} AS BIGINT) AS n',label=f'step/{i}')
                samples.append((time.perf_counter()-started)*1000)
                assert rt.rows(answer)==[[str(9007199254740993+i)]]
        os.kill(pid,signal.SIGTERM)
        deadline=time.monotonic()+5
        while True:
            state=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
            if not state or state.startswith('Z'): break
            assert time.monotonic()<deadline
            time.sleep(.01)
        pid=None
        with RowTrail(str(bins/'rowtrail'),str(workspace)) as rt:
            pid=rt.call('doctor',{})['coordinator_pid']
            for i in range(a.lookups):
                step=(i*2654435761)%a.queries
                started=time.perf_counter()
                page=rt.call('workspace',{'action':'summary','kind':'result','label':f'step/{step}','max_bytes':2048})
                elapsed=(time.perf_counter()-started)*1000
                item,=page['items']
                assert page['next_cursor'] is None and page['counts']['readable_results']==a.queries
                assert item['row_count']==1 and item['schema_hint']['fields'][0]['name']=='n'
                lookups.append({'step':step,'ms':elapsed,'result_json_bytes':len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())})
            answer=rt.query('SELECT n+1 AS n FROM saved',{'saved':item})
            assert rt.rows(answer)==[[str(9007199254740994+step)]]
            assert answer['job']['metrics']['io']['source_read_bytes']==0
        with sqlite3.connect(workspace/'metadata.sqlite') as db:
            indexed=db.execute("EXPLAIN QUERY PLAN SELECT r.id FROM catalog_labels l JOIN results r ON r.id=l.ref WHERE l.label=?",['step/0']).fetchall()
            assert any('catalog_labels_value' in row[-1] for row in indexed)
            assert db.execute('SELECT COUNT(*) FROM catalog_labels').fetchone()[0]==a.queries*2
        report={'status':'passed','kind':'labeled retained history and fresh-coordinator handoff; no model calls','platform':platform.platform(),'queries':a.queries,
                'binary_sha256':{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ('rowtrail','rowtrail-runtime')},
                'early_labeled_query':stats(samples[1:101]),'late_labeled_query':stats(samples[-100:]),'all_query_ms':samples,
                'catalog_lookups':lookups,'catalog_summary':stats([r['ms'] for r in lookups]),'label_query_plan':indexed,
                'workspace_file_bytes':sum(f.stat().st_size for f in workspace.rglob('*') if f.is_file()),'handoff_original_source_bytes':0,
                'limitations':['One retained workspace, not a repeated version comparison.','All results and labels retained; no GC. SQLite/WAL file lengths are not allocated disk space or the managed-data quota.','Catalog timings include protocol/JSON, exclude fresh coordinator startup, and retain the first lookup. Counts still cover the whole workspace.','Backend engineering check; no conclusion about model latency or token savings.']}
        out=ROOT/a.output;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k not in ('binary_sha256','all_query_ms','catalog_lookups')},indent=2))
    finally:
        if pid:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
