"""Optional, real external-agent paired pilot. Never imported by the product.

Requires an authenticated Codex CLI and benchmark-only duckdb==1.5.5. Both arms
get the same persistent Python code environment and may save intermediate work.
Raw events stay local; the report excludes reasoning items and keeps tool traces,
answers, errors, usage and independent arithmetic checks. No automatic retries.
"""
import argparse, collections, contextlib, hashlib, io, json, os, pathlib, signal
import socket, subprocess, sys, tempfile, threading, time
from decimal import Decimal

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail

def oracle(rows,task):
 values=[(9007199254740993+i,None if i%13==0 else ['华东','华南','华北'][i%3],'A' if i%2==0 else 'B',None if i%17==0 else i%2001-1000) for i in range(rows)]
 def money(n):return format(Decimal(n)/100,'.2f')
 if task=='handoff':
  selected=[v for v in values if v[1]=='华东' and v[3] is not None]
  return {'rows':str(len(selected)),'products':[{'product':p,'total':money(sum(v[3] for v in selected if v[2]==p)),'max_id':str(max(v[0] for v in selected if v[2]==p))} for p in ['A','B']]}
 totals=collections.defaultdict(int)
 for _,region,_,amount in values:
  if region is not None and amount is not None:totals[region]+=amount
 region=min(totals,key=lambda k:(-totals[k],k));selected=[v for v in values if v[1]==region and v[3] is not None]
 products={p:sum(v[3] for v in selected if v[2]==p) for p in ['A','B']};product=min(products,key=lambda k:(-products[k],k))
 positive=[v for v in selected if v[3]>0]
 return {'region':region,'region_total':money(totals[region]),'product':product,'product_total':money(products[product]),'positive_count':str(len(positive)),'min_positive_id':str(min(v[0] for v in positive)),'null_region_total':money(sum(v[3] for v in values if v[1] is None and v[3] is not None))}

class LoggedRowTrail:
 def __init__(self,binary,workspace):self.binary=binary;self.workspace=workspace;self.client=RowTrail(str(binary),str(workspace));self.calls=[]
 def call(self,method,params,idempotency_key=None):
  started=time.perf_counter()
  try:
   value=self.client.call(method,params,idempotency_key);self.calls.append({'method':method,'params':params,'result':value,'wall_ms':(time.perf_counter()-started)*1000});return value
  except Exception as e:
   self.calls.append({'method':method,'params':params,'error':str(e),'wall_ms':(time.perf_counter()-started)*1000});raise
 def reconnect(self):
  with contextlib.suppress(Exception):self.client.__exit__()
  self.client=RowTrail(str(self.binary),str(self.workspace))
 def close(self):
  with contextlib.suppress(Exception):
   self.reconnect();pid=self.client.call('doctor',{})['coordinator_pid'];self.client.__exit__();os.kill(pid,signal.SIGTERM)

class LoggedDuckDB:
 def __init__(self,directory):
  import duckdb
  self.connection=duckdb.connect(str(directory/'data.duckdb'));self.connection.execute("SET threads=1");self.connection.execute("SET memory_limit='134217728B'")
  self.connection.execute("PRAGMA enable_profiling='json'");self.directory=directory;self.calls=[]
 def execute(self,sql,params=None):
  path=self.directory/f'profile-{len(self.calls)}.json';path.unlink(missing_ok=True)
  self.connection.execute('PRAGMA profiling_output='+"'"+str(path).replace("'","''")+"'")
  started=time.perf_counter();record={'sql':sql,'params':params,'profile_path':str(path)};self.calls.append(record)
  try:self.connection.execute(sql,params or []);record['execute_ms']=(time.perf_counter()-started)*1000;return self
  except Exception as e:record['error']=str(e);raise
 def __getattr__(self,name):
  if name not in ('fetchall','fetchone','fetchmany','description'):raise AttributeError('Use execute(sql, params) for measured database access')
  return getattr(self.connection,name)
 def close(self):self.connection.close()
 def profiles(self):
  out=[]
  for call in self.calls:
   value=dict(call);path=pathlib.Path(value.pop('profile_path'))
   if path.exists():value['profile']=json.loads(path.read_text())
   out.append(value)
  return out

BRIDGE='''import json,socket,sys
s=socket.socket(socket.AF_UNIX);s.connect("broker.sock")
with s.makefile('rw') as f:
 f.write(json.dumps({'code':sys.stdin.read()})+'\\n');f.flush()
 print(f.readline().strip())
'''

def run_trial(a,task,arm,repeat,fixture,out):
 answer=oracle(a.rows,task);started=time.perf_counter();requests=[];backend=None;server=None
 with tempfile.TemporaryDirectory(prefix='rowtrail-agent-pair-') as td:
  base=pathlib.Path(td);local=out/f'{task}-{repeat}-{arm}';local.mkdir(parents=True,exist_ok=False)
  source=base/'input.parquet';source.symlink_to(fixture)
  env={'__builtins__':__builtins__,'json':json,'Decimal':Decimal,'source':str(source)}
  if arm=='rowtrail':
   backend=LoggedRowTrail(ROOT/a.bin_dir/'rowtrail',base/'workspace');env['rt']=backend
   if task=='handoff':
    opened=backend.call('open',{'source':str(source)})
    result=backend.call('query',{'bindings':{'t':{k:opened[k] for k in ('dataset_ref','manifest_ref')}},'sql':"SELECT id,region,product,amount FROM t WHERE region='华东' AND amount IS NOT NULL",'execution':{'wait_ms':1000}})
    while result['job']['state'] in ('queued','running'):result=backend.call('control',{'action':'wait','ref':result['job']['id'],'wait_ms':1000})
    assert result['job']['state']=='completed';backend.reconnect()
   backend.calls.clear()
   docs=(ROOT/'docs/agent-guide.md').read_text()
   notes='The persistent environment provides rt.call(method, params), returning the result object or raising an error. It uses RowTrail. rt.reconnect() creates a fresh transport without replaying any calls. Use workspace summary to find saved bindings. API request schemas are in schemas.json.\n'+docs
   schemas={name:json.loads(subprocess.check_output([str(ROOT/a.bin_dir/'rowtrail'),'schema',name])) for name in ('open','inspect','query','read','control','workspace')}
   (base/'schemas.json').write_text(json.dumps(schemas))
  else:
   backend=LoggedDuckDB(base);env['con']=backend
   if task=='handoff':backend.execute("CREATE TABLE prior_filtered AS SELECT id,region,product,amount FROM read_parquet(?) WHERE region='华东' AND amount IS NOT NULL",[str(source)])
   backend.calls.clear()
   notes='The persistent environment provides con, a DuckDB connection with execute(sql, params=None), fetchall(), fetchone(), fetchmany() and description. Use execute for all SQL so measurement can record every statement. The connection remains alive across calls, including temporary tables. You may create/materialize intermediate tables, views, reusable Python functions and variables, and use arbitrary read-only or temporary-table SQL. SQL parameters use ?. Discover tables using SHOW TABLES or information_schema; inspect schema using DESCRIBE. Use read_parquet(source) to read the provided source. This is a persistent DuckDB baseline, not a fresh process or connection per query.'
  setup_ms=(time.perf_counter()-started)*1000
  (base/'bridge.py').write_text(BRIDGE);(base/'USAGE.md').write_text(notes)
  server=socket.socket(socket.AF_UNIX);server.bind(str(base/'broker.sock'));server.listen();server.settimeout(.2);stop=threading.Event()
  def serve():
   while not stop.is_set():
    try:client,_=server.accept()
    except socket.timeout:continue
    with client,client.makefile('rw') as f:
     try:
      req=json.loads(f.readline(65537));code=req['code'];before=time.perf_counter();capture=io.StringIO()
      try:
       with contextlib.redirect_stdout(capture):exec(compile(code,'<agent-code>','exec'),env)
       response={'ok':True,'stdout':capture.getvalue()}
      except Exception as e:response={'ok':False,'error':str(e),'stdout':capture.getvalue()}
      response['stdout']=response['stdout'][:32768];response['wall_ms']=(time.perf_counter()-before)*1000
      requests.append({'code':code,'response':response});f.write(json.dumps(response,ensure_ascii=False,default=str)+'\n');f.flush()
     except Exception as e:f.write(json.dumps({'ok':False,'error':str(e)})+'\n');f.flush()
  thread=threading.Thread(target=serve,daemon=True);thread.start()
  if task=='explore':goal='Find the non-null region with greatest total amount, ignoring null amounts; break ties by ascending region. For that region, find the product with greatest total amount (tie by ascending product), the count of strictly positive amounts, and the smallest ID among those positive rows. Also report the sum of amounts for null-region rows across the original source. Money must have two decimal places; IDs/counts must be exact decimal strings.'
  else:goal="A previous session saved the rows with region='华东' and non-null amount, retaining id, region, product and amount. Discover and reuse that materialized result/table. Do not rescan the original Parquet data. Report its row count and, per product in ascending order, total amount (two decimal places) and maximum ID (exact string)."
  schema={'type':'object','additionalProperties':False,'required':list(answer),'properties':{k:{'type':'string'} for k in answer}}
  if task=='handoff':schema['properties']['products']={'type':'array','items':{'type':'object','additionalProperties':False,'required':['product','total','max_id'],'properties':{k:{'type':'string'} for k in ('product','total','max_id')}}}
  (base/'answer.schema.json').write_text(json.dumps(schema))
  prompt=f'''Analyze the synthetic structured data for this task. Read USAGE.md first.
You have a persistent Python environment: variables, functions and the assigned backend survive every call. To execute code, run {sys.executable} bridge.py with Python code on stdin (a shell heredoc is fine). Print only needed bounded observations; never dump the full table. json, Decimal and source (the input file path) are already defined. Use only the assigned backend to access data; do not read source bytes with another engine, inspect broker internals, read outside this task directory, or use network/subagents. You may compose several operations in one code call, save and reuse intermediate work, and choose your own analysis approach. The backend receives no model calls.
Task: {goal}
Return only the final JSON answer matching answer.schema.json. Check accuracy and completeness before answering. Do not change any product code.'''
  (local/'prompt.txt').write_text(prompt.replace(str(base),'<fixture>'));(local/'usage.md').write_text(notes)
  command=['codex','exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','--json','--sandbox','workspace-write','-c','sandbox_workspace_write.network_access=true','-c',f'model="{a.model}"','-c',f'model_reasoning_effort="{a.effort}"','--cd',str(base),'--output-schema',str(base/'answer.schema.json'),'-o',str(base/'answer.json'),prompt]
  begin=time.perf_counter();error=None;returncode=None
  try:
   with (local/'raw-events.jsonl').open('w') as log,(local/'stderr.log').open('w') as err:
    result=subprocess.run(command,stdout=log,stderr=err,text=True,timeout=a.timeout);returncode=result.returncode
  except subprocess.TimeoutExpired:error='timeout'
  wall_ms=(time.perf_counter()-begin)*1000
  stop.set();thread.join(timeout=5);server.close()
  backend_calls=list(backend.calls) if arm=='rowtrail' else backend.profiles()
  backend.close()
  try:actual=json.loads((base/'answer.json').read_text())
  except (OSError,ValueError):actual=None
  events=[];usage=None
  for line in (local/'raw-events.jsonl').read_text().splitlines():
   try:event=json.loads(line)
   except ValueError:continue
   if event.get('type')=='turn.completed':usage=event.get('usage')
   if event.get('item',{}).get('type')!='reasoning':events.append(event)
  source_read=0;result_read=0;source_scan_rows=None
  if arm=='rowtrail':
   jobs={r['result']['job']['id']:r['result']['job'] for r in backend_calls if isinstance(r.get('result',{}).get('job'),dict)}
   source_read=sum((j.get('metrics') or {}).get('io',{}).get('source_read_bytes',0) for j in jobs.values())
   result_read=sum((j.get('metrics') or {}).get('io',{}).get('result_read_bytes',0) for j in jobs.values())
  else:
   source_scan_rows=0
   def scans(node):
    nonlocal source_scan_rows
    if 'READ_PARQUET' in json.dumps(node.get('extra_info',{})).upper():source_scan_rows+=node.get('operator_rows_scanned',0)
    for child in node.get('children',[]):scans(child)
   unmeasured=False
   for r in backend_calls:
    if 'profile' in r:scans(r['profile'])
    elif 'READ_PARQUET' in r['sql'].upper() and 'error' not in r:unmeasured=True
   if unmeasured:source_scan_rows=None
   source_read=None;result_read=None
  reuse_ok=task!='handoff' or (source_read==0 if arm=='rowtrail' else source_scan_rows==0)
  record={'task':task,'repeat':repeat,'arm':arm,'status':'passed' if actual==answer and reuse_ok and returncode==0 else 'failed','error':error,'returncode':returncode,'expected':answer,'actual':actual,'reuse_constraint_passed':reuse_ok,'setup_ms':setup_ms,'agent_wall_ms':wall_ms,'total_ms':setup_ms+wall_ms,'code_calls':len(requests),'code_wall_ms':sum(r['response']['wall_ms'] for r in requests),'observed_response_bytes':sum(len(json.dumps(r['response'],ensure_ascii=False).encode()) for r in requests),'usage':usage,'source_read_bytes':source_read,'result_read_bytes':result_read,'duckdb_source_scan_rows':source_scan_rows,'requests':requests,'backend_calls':backend_calls,'events':events}
  def scrub(v):
   if isinstance(v,str):return v.replace(str(base),'<fixture>').replace(str(fixture),'<input>').replace(str(ROOT),'<repo>').replace(sys.executable,'<benchmark-python>')
   if isinstance(v,list):return [scrub(x) for x in v]
   if isinstance(v,dict):return {k:scrub(x) for k,x in v.items()}
   return v
  record=scrub(record);(local/'record.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps({k:record[k] for k in ('task','arm','repeat','status','agent_wall_ms','code_calls','usage')},ensure_ascii=False),flush=True)
  return record

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model',required=True);p.add_argument('--effort',default='xhigh');p.add_argument('--repeats',type=int,default=3);p.add_argument('--rows',type=int,default=16391);p.add_argument('--timeout',type=int,default=300);p.add_argument('--bin-dir',default='target/release');p.add_argument('--output',default='benchmarks/local/agent-pair');a=p.parse_args()
 out=(ROOT/a.output).resolve();out.mkdir(parents=True,exist_ok=False);records=[]
 with tempfile.TemporaryDirectory(prefix='rowtrail-agent-fixture-') as td:
  data=pathlib.Path(td);subprocess.run([str(ROOT/a.bin_dir/'rowtrail-runtime'),'fixtures','--directory',str(data),'--rows',str(a.rows)],check=True,stdout=subprocess.DEVNULL)
  fixture=data/'many.parquet';fixture_hash=hashlib.file_digest(fixture.open('rb'),'sha256').hexdigest()
  for repeat in range(a.repeats):
   for task in ('explore','handoff'):
    for arm in (('rowtrail','duckdb') if repeat%2==0 else ('duckdb','rowtrail')):records.append(run_trial(a,task,arm,repeat,fixture,out))
  report={'kind':'real external-agent paired pilot','model':a.model,'reasoning_effort':a.effort,'codex_version':subprocess.check_output(['codex','--version'],text=True).strip(),'duckdb_version':__import__('duckdb').__version__,'rows':a.rows,'fixture_sha256':fixture_hash,'binary_sha256':{n:hashlib.file_digest((ROOT/a.bin_dir/n).open('rb'),'sha256').hexdigest() for n in ('rowtrail','rowtrail-runtime')},'conditions':['Same model/settings, task and input for both arms; fresh agent per task; arm order alternates by repeat.','Both use a persistent Python process and may compose calls, cache values and materialize results. DuckDB connection and temporary tables persist.','Handoff setup materializes the same subset before the agent starts; setup time is reported separately and included in total.','No internal product model calls; models run only in this optional external harness.','I/O counters differ: RowTrail reports physical source/result bytes, DuckDB profiles report original-source scanned rows. Null means not measured, never zero.','Small synthetic pilot; not an adoption study or universal speed claim. Retain failures and usage including cached tokens.','Agent wall time includes CLI/model/tool time. Backend code time and emitted observation bytes are separate; emitted bytes are not total model tokens.'],'records':records}
  (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
