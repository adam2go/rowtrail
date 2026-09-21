"""Measure time to a useful file checkpoint and final cost, with full arithmetic oracle."""
import argparse,hashlib,json,os,pathlib,platform,signal,statistics,subprocess,tempfile,time
from decimal import Decimal,ROUND_DOWN
import duckdb
p=argparse.ArgumentParser();p.add_argument('--bin-dir',default='target/release');p.add_argument('--fragment-unit');p.add_argument('--checkpoint-interval-ms',type=int);p.add_argument('--row-group-size',type=int,default=65536);p.add_argument('--rows',type=int,default=1048576);p.add_argument('--files',type=int,default=16);p.add_argument('--repeats',type=int,default=5);p.add_argument('--output',default='benchmarks/local/progressive.json');args=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[1];bins=(root/args.bin_dir).resolve();records=[]
cent_sum=sum(i%2001-1000 for i in range(args.rows) if i%17);count=sum(i%17!=0 for i in range(args.rows));average=format((Decimal(cent_sum)/100/count).quantize(Decimal('.000001'),rounding=ROUND_DOWN),'.6f')
expected=[[str(args.rows),str(count),format(Decimal(cent_sum)/100,'.2f'),average]]
aggregates=[{'function':'count','alias':'rows'},{'function':'count','column':'amount','alias':'nonnull'},{'function':'sum','column':'amount','alias':'total'},{'function':'avg','column':'amount','alias':'mean'}]
with tempfile.TemporaryDirectory(prefix='rowtrail-progressive-bench-') as td:
 base=pathlib.Path(td);subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',str(base/'data'),'--rows',str(args.rows)],check=True,stdout=subprocess.DEVNULL)
 fragments=base/'fragments';fragments.mkdir();con=duckdb.connect();width=(args.rows+args.files-1)//args.files
 for i in range(args.files):
  con.execute('COPY (SELECT id,amount FROM read_parquet($source) WHERE id >= $low AND id < $high) TO $destination (FORMAT PARQUET, COMPRESSION SNAPPY, ROW_GROUP_SIZE '+str(args.row_group_size)+')',{'source':str(base/'data/many.parquet'),'destination':str(fragments/f'{i:03}.parquet'),'low':9007199254740993+i*width,'high':9007199254740993+(i+1)*width})
 con.close()
 for repeat in range(args.repeats):
  for mode in (['sql','progressive'] if repeat%2==0 else ['progressive','sql']):
   workspace=base/f'{repeat}-{mode}';session=subprocess.Popen([str(bins/'rowtrail'),'--workspace',str(workspace),'session'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
   def call(method,params):
    session.stdin.write(json.dumps({'api_version':'1','request_id':'bench','method':method,'params':params})+'\n');session.stdin.flush();r=json.loads(session.stdout.readline());assert r['ok'],r;return r['result']
   opened=call('open',{'source':str(fragments),'format':'parquet'});binding={k:opened[k] for k in ['dataset_ref','manifest_ref']}
   started=time.perf_counter()
   if mode=='progressive':result=call('analyze',{'source':binding,'aggregates':aggregates,**({'fragment_unit':args.fragment_unit} if args.fragment_unit else {}),**({'checkpoint_interval_ms':args.checkpoint_interval_ms} if args.checkpoint_interval_ms is not None else {}),'execution':{'wait_ms':0}})
   else:result=call('query',{'bindings':{'t':binding},'sql':'SELECT rows,nonnull,total,CAST(CAST(total AS DECIMAL(38,6))/CAST(nonnull AS DECIMAL(20,0)) AS DECIMAL(38,6)) AS mean FROM (SELECT COUNT(*) AS rows,COUNT(amount) AS nonnull,SUM(CAST(amount AS DECIMAL(38,2))) AS total FROM t)','execution':{'wait_ms':0}})
   job=result['job']['id'];first=None;first_snapshot=None
   while True:
    state=call('control',{'action':'status','ref':job})['job']
    if state['readable_revision'] and first is None:
     first_snapshot=call('read',{'result_ref':state['result_ref'],'revision':state['readable_revision']});first=(time.perf_counter()-started)*1000
    if state['state'] in ('completed','failed','cancelled','budget_exhausted','interrupted'):break
    time.sleep(.002)
   assert state['state']=='completed',state
   final=call('read',{'result_ref':state['result_ref'],'revision':state['readable_revision']});elapsed=(time.perf_counter()-started)*1000
   assert final['rows']==expected,(mode,final['rows'],expected)
   assert first_snapshot['rows'] and len(first_snapshot['rows'])==1
   if mode=='progressive':
    progress=first_snapshot['quality']['coverage']['input_coverage'];n=progress.get('processed_rows',min(args.rows,progress['completed_files']*width));cents=sum(i%2001-1000 for i in range(n) if i%17);non_null=sum(i%17!=0 for i in range(n));mean=format((Decimal(cents)/100/non_null).quantize(Decimal('.000001'),rounding=ROUND_DOWN),'.6f')
    assert first_snapshot['rows']==[[str(n),str(non_null),format(Decimal(cents)/100,'.2f'),mean]]
   pid=call('doctor',{})['coordinator_pid'];session.stdin.close();session.wait(timeout=5);os.kill(pid,signal.SIGTERM)
   records.append({'repeat':repeat,'mode':mode,'first_observed_ms':first,'final_ms':elapsed,'first_rows':first_snapshot['rows'],'first_quality':first_snapshot['quality'],'final_rows':final['rows'],'metrics':state['metrics']})
 def scrub(v):
  if isinstance(v,str):return v.replace(str(base),'<fixture>').replace(str(base).lstrip('/'),'<fixture>')
  if isinstance(v,list):return [scrub(x) for x in v]
  if isinstance(v,dict):return {k:scrub(x) for k,x in v.items()}
  return v
 summary={mode:{k:statistics.median(r[k] for r in records if r['mode']==mode) for k in ['first_observed_ms','final_ms']} for mode in ['sql','progressive']}
 report={'kind':'exact fragment checkpoints versus an ordinary complete SQL aggregate','fragment_unit_request':args.fragment_unit,'checkpoint_interval_ms_request':args.checkpoint_interval_ms,'row_group_size':args.row_group_size,'rows':args.rows,'files':args.files,'source_bytes':sum(f.stat().st_size for f in fragments.glob('*.parquet')),'os':platform.platform(),'machine':platform.machine(),'binary_sha256':{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ['rowtrail','rowtrail-runtime']},'records':scrub(records),'summary_ms':summary,'conditions':['Five serial repeats, alternating modes, warm OS cache, fresh workspace/session each run.','Open and fixture creation are excluded; worker startup, observation and checkpoint publication are included.','Poll interval 2 ms; observed first-result time is an upper bound on availability.','Every final answer and every first observed prefix matches independent Python integer/Decimal arithmetic.','Progressive partials describe only committed fragments in reported coverage; they are not population estimates.','Final cost includes all checkpoints, including unused old revisions.']}
 path=root/args.output;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,indent=2))
