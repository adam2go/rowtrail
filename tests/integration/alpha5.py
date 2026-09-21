"""Row-group prefixes, bounded publication, reconnect inventory and local setup."""
import argparse, hashlib, json, os, pathlib, signal, sqlite3, subprocess, sys, tempfile, time
from decimal import Decimal, ROUND_DOWN
p=argparse.ArgumentParser();p.add_argument('--bin-dir',default='target/release');p.add_argument('--report',default='benchmarks/local/alpha5.json');a=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[2];bins=(root/a.bin_dir).resolve();sys.path.insert(0,str(root/'examples'))
from session_client import RowTrail
checks=[];traces=[];succeeded=False
def check(name):checks.append(name);print('PASS',name,flush=True)
with tempfile.TemporaryDirectory(prefix='rowtrail-alpha5-') as td:
 base=pathlib.Path(td);ws=base/'workspace';pid=None
 try:
  # Discovery/configuration never starts the coordinator or creates a workspace.
  untouched=base/'untouched'
  guide=json.loads(subprocess.check_output([str(bins/'rowtrail'),'--workspace',str(untouched),'guide']))
  config=json.loads(subprocess.check_output([str(bins/'rowtrail'),'--workspace',str(untouched),'mcp-config']))
  assert guide['api_version']=='1' and config['mcpServers']['rowtrail']['args']==['--workspace',str(untouched),'mcp']
  assert not untouched.exists()
  check('guide and MCP configuration are valid JSON and start no runtime')
  subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',str(base/'data'),'--rows','16391'],check=True,stdout=subprocess.DEVNULL)
  client=RowTrail(str(bins/'rowtrail'),str(ws))
  def call(method,params,ok=True):
   request={'api_version':'1','request_id':str(len(traces)),'method':method,'params':params};client.process.stdin.write(json.dumps(request)+'\n');client.process.stdin.flush();r=json.loads(client.process.stdout.readline());traces.append({'request':request,'response':r})
   if ok:assert r['ok'],r
   return r['result'] if r['ok'] else r
  def wait(r,success=True):
   deadline=time.monotonic()+30
   while r['job']['state'] in ('queued','running','stopping'):
    assert time.monotonic()<deadline,r
    r=call('control',{'action':'wait','ref':r['job']['id'],'wait_ms':1000})
   if success:assert r['job']['state']=='completed',r
   return r
  def bind(d):return {k:d[k] for k in ('dataset_ref','manifest_ref')}
  def ref(r,rev=None):return {'result_ref':r['job']['result_ref'],'revision':rev or r['readable_revision']}
  def read(r,rev=None):return call('read',ref(r,rev))
  def query(sql,bindings=None):return wait(call('query',{'bindings':bindings or {},'sql':sql,'execution':{'wait_ms':1000}}))
  pid=call('doctor',{})['coordinator_pid']
  opened=call('open',{'source':str(base/'data/many.parquet')});source=bind(opened)
  aggregates=[{'function':'count','alias':'n'},{'function':'count','column':'amount','alias':'nn'},{'function':'sum','column':'amount','alias':'s'},{'function':'avg','column':'amount','alias':'a'},{'function':'sum','column':'id','alias':'ids'}]
  def expected(n):
   cents=[i%2001-1000 for i in range(n) if i%17];total=sum(cents)
   mean=format((Decimal(total)/100/len(cents)).quantize(Decimal('.000001'),rounding=ROUND_DOWN),'.6f')
   return [[str(n),str(len(cents)),format(Decimal(total)/100,'.2f'),mean,str(n*9007199254740993+n*(n-1)//2)]]
  done=wait(call('analyze',{'source':source,'aggregates':aggregates,'checkpoint_interval_ms':0,'execution':{'wait_ms':1000}}))
  assert done['readable_revision']==6,done
  for rev in range(1,6):
   r=read(done,rev);coverage=r['quality']['coverage']['input_coverage'];n=min(rev*4096,16391)
   assert r['rows']==expected(n),(rev,r)
   assert coverage['unit']=='parquet_row_group' and coverage['completed_fragments']==rev and coverage['total_fragments']==5 and coverage['processed_rows']==n
   assert coverage['completed_files']==(1 if rev==5 else 0)
   assert r['quality']['coverage']['kind']=='partial' and not r['quality']['final_for_request']
  assert read(done)['rows']==expected(16391) and read(done)['quality']['final_for_request']
  check('every single-file row-group checkpoint matches independent integer/Decimal arithmetic including uneven tail')
  silent=wait(call('analyze',{'source':source,'aggregates':aggregates,'execution':{'preview':'none','wait_ms':1000}}))
  coalesced=wait(call('analyze',{'source':source,'aggregates':aggregates,'checkpoint_interval_ms':60000,'execution':{'wait_ms':1000}}))
  assert silent['readable_revision']==1 and coalesced['readable_revision']==3
  assert read(silent)['rows']==read(coalesced)['rows']==expected(16391)
  assert silent['job']['metrics']['io']['source_read_bytes']==coalesced['job']['metrics']['io']['source_read_bytes']
  check('coalescing publishes first/final prefixes with unchanged scan bytes; preview none stores one result')
  count=wait(call('analyze',{'source':source,'aggregates':[{'function':'count','alias':'n'}],'execution':{'wait_ms':1000}}))
  assert read(count)['rows']==[['16391']]
  check('count-only row-group execution preserves row counts with empty column projection')
  branch=query('SELECT n,s FROM p',{'p':ref(done,2)});assert read(branch)['rows']==[expected(8192)[0][:1]+expected(8192)[0][2:3]]
  assert branch['job']['metrics']['io']['source_read_bytes']==0 and read(branch)['quality']['coverage']['kind']=='partial'
  check('partial row-group revision remains immutable and reusable without original-source reads')
  db=sqlite3.connect(ws/'metadata.sqlite');first_bytes=db.execute('SELECT bytes FROM parts WHERE result_id=? AND seq=0',(ref(done)['result_ref'],)).fetchone()[0]
  exhausted=wait(call('analyze',{'source':source,'aggregates':aggregates,'checkpoint_interval_ms':0,'execution':{'result_bytes':first_bytes+1,'wait_ms':1000}}),False)
  assert exhausted['job']['state']=='budget_exhausted' and exhausted['readable_revision']==1,exhausted
  assert read(exhausted)['rows']==expected(4096)
  denied=wait(call('analyze',{'source':source,'aggregates':aggregates,'execution':{'scan_bytes':1,'wait_ms':1000}}),False)
  assert denied['job']['state']=='budget_exhausted' and denied['readable_revision'] is None,denied
  check('result and scan budgets preserve only complete committed row-group prefixes')
  for params in [{'fragment_unit':'batch'},{'checkpoint_interval_ms':60001}]:assert not call('analyze',{'source':source,'aggregates':aggregates,**params},False)['ok']
  check('unsupported fragments and unbounded publication intervals fail explicitly')
  empty=query('SELECT CAST(NULL AS BIGINT) n WHERE false');destination=base/'empty.parquet';wait(call('export',{**ref(empty),'format':'parquet','destination':str(destination)}));e=call('open',{'source':str(destination)})
  zero=wait(call('analyze',{'source':bind(e),'aggregates':[{'function':'count','alias':'n'},{'function':'sum','column':'n','alias':'s'}],'execution':{'wait_ms':1000}}))
  assert read(zero)['rows']==[['0',None]] and read(zero)['quality']['final_for_request']
  check('zero-row Parquet completes with count zero, typed null and explicit coverage')
  csv=call('open',{'source':str(base/'data/small.csv'),'schema':json.loads((root/'tests/fixtures/manifest.json').read_text())['small']['schema']});prepared=wait(call('prepare',{'source':bind(csv),'execution':{'wait_ms':1000}}))
  managed=wait(call('analyze',{'source':prepared['job']['prepared'],'aggregates':[{'function':'count','alias':'n'}],'execution':{'wait_ms':1000}}));assert read(managed)['rows']==[['8']]
  assert managed['job']['metrics']['io']['result_read_bytes']>0
  check('managed prepared Parquet retains checksum verification and accurate discovered fragment totals')
  subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',str(base/'large'),'--rows','1048576'],check=True,stdout=subprocess.DEVNULL)
  large=call('open',{'source':str(base/'large/many.parquet')})
  for stop_mode in ('cancel','kill'):
   active=call('analyze',{'source':bind(large),'aggregates':aggregates,'checkpoint_interval_ms':0,'execution':{'wait_ms':0}})
   deadline=time.monotonic()+15
   while not active['readable_revision']:
    assert time.monotonic()<deadline and active['job']['state'] in ('queued','running'),active
    active=call('control',{'action':'wait','ref':active['job']['id'],'wait_ms':1})
   revision=active['readable_revision'];before=read(active,revision)
   if stop_mode=='cancel':call('control',{'action':'cancel','ref':active['job']['id']})
   else:
    worker=db.execute('SELECT worker_pid FROM jobs WHERE id=?',(active['job']['id'],)).fetchone()[0];os.kill(worker,signal.SIGKILL)
   stopped=wait(active,False)
   assert stopped['job']['state']==('cancelled' if stop_mode=='cancel' else 'interrupted'),stopped
   assert read(stopped,revision)['rows']==before['rows'] and read(stopped)['quality']['coverage']['kind']=='partial'
   assert stopped['job']['metrics']['worker_exit_confirmed'],stopped
  check('cancellation and worker death preserve immutable single-file row-group checkpoints and confirm execution stopped')
  # A new client knows only the workspace. Membership is fixed while values remain live.
  client.__exit__();client=RowTrail(str(bins/'rowtrail'),str(ws))
  page=call('workspace',{'action':'summary','kind':'result','limit':1,'max_bytes':2048});items=list(page['items']);cursor=page['next_cursor'];assert cursor
  late=query('SELECT 999 late')
  while cursor:
   page=call('workspace',{'action':'summary','kind':'result','limit':1,'max_bytes':2048,'cursor':cursor});items+=page['items'];cursor=page['next_cursor']
  assert len({x['ref'] for x in items})==len(items) and ref(late)['result_ref'] not in [x['ref'] for x in items]
  recovered=next(x for x in items if x['ref']==ref(done)['result_ref']);assert call('read',recovered['binding'])['rows']==expected(16391)
  assert recovered['quality']['coverage']['input_coverage']['total_fragments']==5
  assert not call('workspace',{'action':'summary','kind':'job','cursor':items and call('workspace',{'action':'summary','kind':'result','limit':1})['next_cursor']},False)['ok']
  check('fresh-session catalog recovers fixed bindings; pagination excludes later objects and rejects changed filters')
  call('control',{'action':'release','ref':ref(late)['result_ref']});call('workspace',{'action':'gc','dry_run':False})
  all_items=[];cursor=None
  while True:
   page=call('workspace',{'action':'summary','cursor':cursor,'max_bytes':8192});all_items+=page['items'];cursor=page['next_cursor']
   if not cursor:break
  expired=next(x for x in all_items if x['kind']=='result' and x['ref']==ref(late)['result_ref']);assert expired['stored_validity']=='expired' and not expired['next_actions']
  err=call('read',ref(late),False);assert err['error']['code']=='OBJECT_EXPIRED' and 'suggestion' in err['error']['details']
  call('control',{'action':'release','ref':ref(managed)['result_ref']})
  call('control',{'action':'release','ref':prepared['job']['prepared']['dataset_ref']})
  call('workspace',{'action':'gc','dry_run':False})
  page=call('workspace',{'action':'summary','kind':'dataset','max_bytes':16384})
  expired_data=next(x for x in page['items'] if x['ref']==prepared['job']['prepared']['dataset_ref'])
  assert expired_data['stored_validity']=='expired' and not expired_data['next_actions']
  check('catalog retains expiration tombstones and errors expose actionable recovery guidance')
  succeeded=True
 finally:
  if 'client' in locals():client.__exit__()
  if pid:
   try:os.kill(pid,signal.SIGTERM)
   except ProcessLookupError:pass
  report={'status':'passed' if succeeded else 'incomplete','checks':checks,'binary_sha256':{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ('rowtrail','rowtrail-runtime')},'traces':traces}
  out=root/a.report;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':report['status'],'checks':len(checks)}))
