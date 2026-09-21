"""Alternate binary order for repeated, complete exploration comparisons."""
import argparse, json, pathlib, statistics, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--variant',action='append',required=True);p.add_argument('--rows',type=int,default=1048576);p.add_argument('--repeats',type=int,default=7);p.add_argument('--output',required=True);a=p.parse_args()
variants=dict(v.split('=',1) for v in a.variant);records=[];metadata={};out=ROOT/a.output;out.parent.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='rowtrail-matrix-') as td:
 for repeat in range(a.repeats):
  names=list(variants)
  if repeat%2:names.reverse()
  for name in names:
   path=pathlib.Path(td)/'trial.json'
   subprocess.run([sys.executable,str(ROOT/'benchmarks/compare.py'),'--entry','session','--rows',str(a.rows),'--repeats','1','--bin-dir',variants[name],'--output',str(path)],check=True,stdout=subprocess.DEVNULL)
   value=json.loads(path.read_text());metadata[name]={k:v for k,v in value.items() if k not in ('records','summary_ms')}
   records.append({'variant':name,'repeat':repeat,**{k:v for k,v in value['records'][0].items() if k!='repeat'}})
   out.write_text(json.dumps({'status':'in_progress','variants':metadata,'records':records},indent=2)+'\n')
 assert len({v['fixture_sha256'] for v in metadata.values()})==1
 summary={name:{backend:statistics.median(r[backend]['total_ms'] for r in records if r['variant']==name) for backend in ('rowtrail','duckdb','datafusion')} for name in variants}
 report={'status':'passed','kind':'alternating binary order; complete five-query exploration with persistent sessions','rows':a.rows,'repeats':a.repeats,'variants':metadata,'records':records,'summary_ms':summary,'limitations':['Order reverses on alternating repeats; each trial has a fresh workspace and freshly generated identical deterministic input.','OS cache is not flushed. Fixtures, process cleanup and report writing are outside the measured workflow.','No concurrent local builds or benchmark processes. Direct engines retain intermediate tables; RowTrail persists them. No model calls.']}
 out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(summary,indent=2))
