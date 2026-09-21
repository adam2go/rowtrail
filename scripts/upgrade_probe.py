"""Verify one-way metadata upgrades using actual old and new binaries."""
import argparse,json,os,pathlib,signal,sqlite3,subprocess,tempfile,time
p=argparse.ArgumentParser();p.add_argument('old_bin_dir');p.add_argument('new_bin_dir');p.add_argument('--old-schema',default='3');p.add_argument('--new-schema',default='5');p.add_argument('--report',default='benchmarks/local/upgrade.json');a=p.parse_args()
old=pathlib.Path(a.old_bin_dir).resolve();new=pathlib.Path(a.new_bin_dir).resolve();pids=[]
with tempfile.TemporaryDirectory(prefix='rowtrail-upgrade-') as td:
 ws=pathlib.Path(td)/'workspace'
 def call(bins,method,params):
  r=subprocess.run([str(bins/'rowtrail'),'--workspace',str(ws),'call',method],input=json.dumps(params),text=True,capture_output=True,check=True,timeout=20);v=json.loads(r.stdout);assert v['ok'],v;return v['result']
 def stop(pid):
  os.kill(pid,signal.SIGTERM)
  until=time.monotonic()+5
  while True:
   state=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
   if not state or state.startswith('Z'):return
   assert time.monotonic()<until
   time.sleep(.01)
 try:
  created=call(old,'query',{'bindings':{},'sql':'SELECT CAST(9007199254740993 AS BIGINT) n','execution':{'wait_ms':1000}})
  while created['job']['state']!='completed':created=call(old,'control',{'action':'wait','ref':created['job']['id'],'wait_ms':1000})
  ref={'result_ref':created['job']['result_ref'],'revision':created['readable_revision']}
  assert call(old,'read',ref)['rows']==[['9007199254740993']]
  with sqlite3.connect(ws/'metadata.sqlite') as c:assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]==a.old_schema
  partial=None
  if int(a.old_schema)>=4:
   fixture=pathlib.Path(td)/'data';subprocess.run([str(old/'rowtrail-runtime'),'fixtures','--directory',str(fixture),'--rows','8'],check=True,stdout=subprocess.DEVNULL)
   opened=call(old,'open',{'source':str(fixture/'small.parquet')})
   analysis=call(old,'analyze',{'source':{k:opened[k] for k in ('dataset_ref','manifest_ref')},'aggregates':[{'function':'count','alias':'n'}],'execution':{'wait_ms':1000}})
   while analysis['job']['state']=='running':analysis=call(old,'control',{'action':'wait','ref':analysis['job']['id'],'wait_ms':1000})
   assert analysis['job']['state']=='completed',analysis
   partial={'result_ref':analysis['job']['result_ref'],'revision':1}
   assert call(old,'read',partial)['rows']==[['8']]
  old_pid=call(old,'doctor',{})['coordinator_pid'];pids.append(old_pid);stop(old_pid);pids.remove(old_pid)
  assert call(new,'read',ref)['rows']==[['9007199254740993']]
  with sqlite3.connect(ws/'metadata.sqlite') as c:assert c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]==a.new_schema
  if partial:
   checkpoint=call(new,'read',partial)
   assert checkpoint['rows']==[['8']] and checkpoint['quality']['coverage']['kind']=='partial'
   assert checkpoint['quality']['coverage']['input_coverage']['unit']=='manifest_file'

  fresh=call(new,'query',{'bindings':{'t':ref},'sql':'SELECT n+1 AS n FROM t','execution':{'wait_ms':1000}})
  assert fresh['job']['state']=='completed'
  assert call(new,'read',{'result_ref':fresh['job']['result_ref'],'revision':fresh['readable_revision']})['rows']==[['9007199254740994']]
  new_pid=call(new,'doctor',{})['coordinator_pid'];pids.append(new_pid);stop(new_pid);pids.remove(new_pid)
  refused=subprocess.run([str(old/'rowtrail-runtime'),'serve','--workspace',str(ws)],capture_output=True,text=True,timeout=10)
  assert refused.returncode!=0 and 'PROTOCOL_VERSION_MISMATCH' in refused.stderr,refused
  report={'status':'passed','old_version':subprocess.check_output([str(old/'rowtrail'),'--version'],text=True).strip(),'new_version':subprocess.check_output([str(new/'rowtrail'),'--version'],text=True).strip(),'old_schema':a.old_schema,'new_schema':a.new_schema,'old_partial_checkpoint_preserved':partial is not None,'old_fixed_revision_preserved':True,'new_query_on_old_result':True,'old_runtime_rejects_upgraded_store':True}
  path=pathlib.Path(a.report);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
 finally:
  for pid in pids:
   try:os.kill(pid,signal.SIGTERM)
   except ProcessLookupError:pass
