"""Alternate real 32 MiB spill runs, independently checking every sorted ID."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--variant',action='append',required=True)
p.add_argument('--rows',type=int,default=1048576)
p.add_argument('--repeats',type=int,default=5)
p.add_argument('--output',default='benchmarks/local/resource-matrix.json')
a=p.parse_args()
variants=dict(v.split('=',1) for v in a.variant)
records=[]
with tempfile.TemporaryDirectory(prefix='rowtrail-resource-matrix-') as td:
    for repeat in range(a.repeats):
        names=list(variants)
        if repeat%2:names.reverse()
        for name in names:
            output=Path(td)/'trial.json'
            subprocess.run([sys.executable,str(ROOT/'benchmarks/resources.py'),'--bin-dir',variants[name],'--rows',str(a.rows),'--output',str(output)],check=True,stdout=subprocess.DEVNULL)
            value=json.loads(output.read_text())
            assert value['status']=='passed' and value['all_exported_ids_match_independent_sort']
            records.append({'variant':name,'repeat':repeat,**value})
    assert len({r['fixture_sha256'] for r in records})==1
    keys=('sort_wall_ms_including_observation','export_wall_ms','spill_count','result_parts','largest_part_bytes','sampled_worker_peak_rss_bytes')
    report={'status':'passed','kind':__doc__,'rows':a.rows,'repeats':a.repeats,'records':records,
            'summary':{name:{key:statistics.median(r[key] for r in records if r['variant']==name) for key in keys} for name in variants},
            'limitations':['One Mac; serial alternating fresh workspaces, OS caches not flushed, no concurrent local build/timing workload.','Sort timing includes protocol, bounded polling and ps RSS samples; this is not pure engine CPU time.','32 MiB is the engine pool, not RSS; samples can miss true peaks.','Every exported ID is checked using an independent Python ordering; DuckDB is only the Parquet reader.']}
    output=ROOT/a.output;output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report['summary'],indent=2))
