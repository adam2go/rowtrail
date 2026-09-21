"""Verify alpha.5/alpha.6 interoperability using actual old/new executables."""
import argparse, hashlib, json, os, pathlib, signal, sqlite3, subprocess, sys, tempfile, time
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail
p=argparse.ArgumentParser();p.add_argument('old_bin_dir');p.add_argument('new_bin_dir');p.add_argument('--report',default='benchmarks/local/storage-compat.json');a=p.parse_args()
old=pathlib.Path(a.old_bin_dir).resolve();new=pathlib.Path(a.new_bin_dir).resolve();active=[]
with tempfile.TemporaryDirectory(prefix='rowtrail-storage-compat-') as td:
 base=pathlib.Path(td);ws=base/'workspace'
 def session(bins):
  rt=RowTrail(str(bins/'rowtrail'),str(ws));pid=rt.call('doctor',{})['coordinator_pid'];active.append(pid);return rt,pid
 def stop(rt,pid):
  rt.__exit__();os.kill(pid,signal.SIGTERM);end=time.monotonic()+10
  while True:
   state=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
   if not state or state.startswith('Z'):break
   assert time.monotonic()<end
   time.sleep(.01)
  active.remove(pid)
 def query(rt,sql,binding):
  r=rt.finish(rt.call('query',{'bindings':{'t':binding},'sql':sql,'execution':{'preview':'none','wait_ms':1000}}));assert r['job']['state']=='completed',r
  return {'result_ref':r['job']['result_ref'],'revision':r['readable_revision']}
 try:
  subprocess.run([str(old/'rowtrail-runtime'),'fixtures','--directory',str(base/'data'),'--rows','65537'],check=True,stdout=subprocess.DEVNULL)
  rt,pid=session(old);opened=rt.call('open',{'source':str(base/'data/many.parquet')});binding={k:opened[k] for k in ('dataset_ref','manifest_ref')}
  legacy=query(rt,'SELECT id,region,amount FROM t',binding)
  analysis=rt.finish(rt.call('analyze',{'source':binding,'aggregates':[{'function':'count','alias':'n'}],'checkpoint_interval_ms':0,'execution':{'wait_ms':1000}}))
  prefix={'result_ref':analysis['job']['result_ref'],'revision':1};expected_prefix=rt.call('read',prefix)
  stop(rt,pid)
  rt,pid=session(new);assert rt.call('read',legacy)['rows'][0]==[['9007199254740993',None,None]][0]
  assert rt.call('read',prefix)['quality']==expected_prefix['quality'] and rt.call('read',prefix)['rows']==expected_prefix['rows']
  compressed=query(rt,'SELECT * FROM t',legacy)
  with sqlite3.connect(ws/'metadata.sqlite') as db:
   assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='5'
   sizes=dict(db.execute('SELECT result_id,SUM(bytes) FROM parts GROUP BY result_id'))
  assert sizes[compressed['result_ref']]<sizes[legacy['result_ref']]/2
  stop(rt,pid)
  rt,pid=session(old);assert rt.call('read',compressed)['rows'][0]==['9007199254740993',None,None]
  derived=query(rt,'SELECT COUNT(*),MAX(id) FROM t',compressed)
  assert rt.call('read',derived)['rows']==[['65537','9007199254806529']]
  stop(rt,pid)
  rt,pid=session(new);assert rt.call('read',derived)['rows']==[['65537','9007199254806529']];stop(rt,pid)
  report={'status':'passed','old_version':subprocess.check_output([str(old/'rowtrail'),'--version'],text=True).strip(),'new_version':subprocess.check_output([str(new/'rowtrail'),'--version'],text=True).strip(),'metadata_schema':'5','old_plain_result_readable':True,'old_fixed_partial_quality_preserved':True,'old_binary_reads_and_queries_new_compressed_result':True,'new_binary_reads_old_derived_result':True,'binary_sha256':{label:{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ('rowtrail','rowtrail-runtime')} for label,bins in [('old',old),('new',new)]}}
  out=ROOT/a.report;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
 finally:
  for pid in active:
   try:os.kill(pid,signal.SIGTERM)
   except ProcessLookupError:pass
