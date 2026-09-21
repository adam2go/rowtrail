"""Compressed durable results, exact page bounds and optional agent projections."""
import argparse, hashlib, json, os, pathlib, signal, sqlite3, subprocess, sys, tempfile
from decimal import Decimal
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'examples'))
from session_client import RowTrail
p=argparse.ArgumentParser();p.add_argument('--bin-dir',default='target/release');p.add_argument('--report',default='benchmarks/local/alpha6.json');a=p.parse_args()
bins=(ROOT/a.bin_dir).resolve();checks=[];traces=[];passed=False
with tempfile.TemporaryDirectory(prefix='rowtrail-alpha6-') as td:
 base=pathlib.Path(td);ws=base/'workspace';pid=None
 try:
  subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',str(base/'data'),'--rows','65537'],check=True,stdout=subprocess.DEVNULL)
  rt=RowTrail(str(bins/'rowtrail'),str(ws))
  def call(method,params,ok=True):
   req={'api_version':'1','request_id':str(len(traces)),'method':method,'params':params}
   rt.process.stdin.write(json.dumps(req)+'\n');rt.process.stdin.flush();reply=json.loads(rt.process.stdout.readline());traces.append({'request':req,'response':reply})
   if ok:assert reply['ok'],reply
   return reply['result'] if reply['ok'] else reply
  def check(name):checks.append(name);print('PASS',name,flush=True)
  def query(sql,binding=None,execution=None,success=True):
   result=call('query',{'bindings':{'t':binding} if binding else {},'sql':sql,'execution':{'wait_ms':1000,'preview':'none',**(execution or {})}})
   result=rt.finish(result)
   if success:assert result['job']['state']=='completed',result
   return result
  def ref(result):return {'result_ref':result['job']['result_ref'],'revision':result['readable_revision']}
  pid=call('doctor',{})['coordinator_pid'];opened=call('open',{'source':str(base/'data/many.parquet')});binding={k:opened[k] for k in ('dataset_ref','manifest_ref')}
  sql='SELECT id,region,amount FROM t';material=query(sql,binding);saved=ref(material)
  db=sqlite3.connect(ws/'metadata.sqlite')
  parts=db.execute('SELECT path,bytes,checksum FROM parts WHERE result_id=? ORDER BY seq',[saved['result_ref']]).fetchall()
  stored=sum(size for _,size,_ in parts)
  assert stored<65537*16 and max(size for _,size,_ in parts)<=8388608
  for path,size,digest in parts:
   assert pathlib.Path(path).stat().st_size==size and hashlib.file_digest(open(path,'rb'),'sha256').hexdigest()==digest
  expected_sum=format(Decimal(sum(i%2001-1000 for i in range(65537) if i%17))/100,'.2f')
  branch=query('SELECT COUNT(*),SUM(amount),MAX(id) FROM t',saved)
  assert call('read',ref(branch))['rows']==[['65537',expected_sum,str(9007199254740993+65536)]]
  assert branch['job']['metrics']['io']['source_read_bytes']==0
  assert branch['job']['metrics']['io']['result_read_bytes']==stored
  check('compressed parts retain exact nullable Decimal/large integers and checksum-accounted source-free reuse')
  exact=query(sql,binding,{'result_bytes':stored});assert exact['job']['metrics']['result_write_bytes']==stored
  denied=query(sql,binding,{'result_bytes':stored-1},False)
  assert denied['job']['state']=='budget_exhausted' and denied['readable_revision'] is None,denied
  check('encoded-byte budget accepts the exact file size and rejects one byte less without publishing final-only output')
  name='金额\n"\\';alias=name.replace('"','""')
  result=query(f'SELECT id,region,amount AS "{alias}" FROM t ORDER BY id LIMIT 1007',binding)
  expected=[[str(9007199254740993+i),None if i%13==0 else ['华东','华南','华北'][i%3],None if i%17==0 else format(Decimal(i%2001-1000)/100,'.2f')] for i in range(1007)]
  for budget in (2048,4096,8192):
   cursor=None;rows=[]
   while True:
    page=call('read',{**ref(result),'max_rows':1000,'max_bytes':budget,'cursor':cursor,'columns':['id','region',name]})
    assert len(json.dumps(traces[-1]['response'],ensure_ascii=False,separators=(',',':')).encode())<=budget
    assert page['schema'][2]['name']==name and page['presentation']['returned_rows']==len(page['rows'])
    assert page['presentation']['has_more']==bool(page['next_cursor'])
    rows.extend(page['rows']);cursor=page['next_cursor']
    if cursor is None:break
    assert page['rows'] and len(rows)<=1007
   assert rows==expected
  check('byte-bounded projected pages preserve escaped Unicode names, nulls, Decimal strings and every cursor row')
  for statement,expected_small in [('SELECT CAST(NULL AS BIGINT) n WHERE false',[]),('SELECT CAST(NULL AS BIGINT) n',[[None]]),('SELECT CAST(9007199254740993 AS BIGINT) n',[['9007199254740993']])]:
   value=query(statement);assert call('read',ref(value))['rows']==expected_small
  check('small and empty uncompressed results keep their original typed observation semantics')
  projection=rt.observe(material)
  assert projection['binding']==saved and projection['state']=='completed'
  assert projection['observation']==material['observation'] and projection['error'] is None
  assert len(json.dumps(projection))<len(json.dumps(material))
  assert rt.schema('query')['type']=='object'
  check('optional compact helper preserves the entire observation, fixed revision and errors while dropping operational detail')
  class Fake:
   finish=RowTrail.finish
   def __init__(self):self.calls=[]
   def call(self,method,params):self.calls.append((method,params));raise ConnectionError('ambiguous transport')
  fake=Fake();running={'job':{'id':'job_test','state':'running'}}
  assert fake.finish(running,timeout=0)==running and fake.calls==[]
  try:fake.finish(running)
  except ConnectionError:pass
  else:raise AssertionError('transport failure was swallowed')
  assert len(fake.calls)==1 and fake.calls[0][0]=='control'
  partial={'job':{'id':'j','state':'budget_exhausted','result_ref':'r','error':{'code':'RESOURCE_EXHAUSTED'}},'readable_revision':2,'observation':{'quality':{'coverage':{'kind':'partial'},'final_for_request':False},'presentation':{'has_more':True},'next_cursor':'cursor'}}
  assert RowTrail.observe(partial)['observation']==partial['observation'] and RowTrail.observe(partial)['error']==partial['job']['error']
  check('helper deadlines do not cancel or replay jobs and partial failure projections retain coverage and truncation')
  rt.__exit__();rt=RowTrail(str(bins/'rowtrail'),str(ws))
  assert call('read',saved)['rows'][0]==expected[0]
  check('fresh transport reads the same immutable compressed revision')
  path=pathlib.Path(parts[0][0]);stat=path.stat();data=bytearray(path.read_bytes());data[len(data)//2]^=1;path.write_bytes(data);os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
  assert call('read',saved,False)['error']['code']=='RESULT_CORRUPT'
  failed=query('SELECT COUNT(*) FROM t',saved,success=False);assert failed['job']['error']['code']=='RESULT_CORRUPT',failed
  exported=rt.finish(call('export',{**saved,'format':'parquet','destination':str(base/'bad.parquet'),'execution':{'wait_ms':1000}}))
  assert exported['job']['error']['code']=='RESULT_CORRUPT' and not (base/'bad.parquet').exists()
  check('same-size same-mtime compressed corruption fails read, query and export before decoding')
  passed=True
 finally:
  if 'rt' in locals():rt.__exit__()
  if pid:
   try:os.kill(pid,signal.SIGTERM)
   except ProcessLookupError:pass
  report={'status':'passed' if passed else 'incomplete','checks':checks,'binary_sha256':{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ('rowtrail','rowtrail-runtime')},'traces':traces}
  output=ROOT/a.report;output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
