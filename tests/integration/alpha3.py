"""Demand-driven profile, managed preparation, integrity and retention contracts."""
from workloads import LONG_QUERY
import argparse, hashlib, json, os, pathlib, signal, sqlite3, subprocess, tempfile, time
from decimal import Decimal

parser = argparse.ArgumentParser()
parser.add_argument('--bin-dir', default='target/release')
parser.add_argument('--report', default='benchmarks/local/alpha3.json')
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[2]
bins = (root / args.bin_dir).resolve()
traces, checks = [], []
terminal = {'completed', 'failed', 'cancelled', 'budget_exhausted', 'interrupted'}
with tempfile.TemporaryDirectory(prefix='rowtrail-alpha3-') as td:
    base = pathlib.Path(td)
    ws = base / 'workspace'
    session = subprocess.Popen([str(bins/'rowtrail'), '--workspace', str(ws), 'session'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def call(method, params, ok=True, key=None):
        request = {'api_version':'1','request_id':str(len(traces)), 'method':method,'params':params}
        if key: request['idempotency_key'] = key
        session.stdin.write(json.dumps(request)+'\n'); session.stdin.flush()
        response = json.loads(session.stdout.readline())
        traces.append({'request':request,'response':response})
        if ok: assert response['ok'], response
        return response['result'] if response['ok'] else response
    def wait(v, success=True):
        job = v.get('job_id') or v['job']['id']
        end = time.monotonic()+40
        while v.get('job',{}).get('state') not in terminal:
            assert time.monotonic()<end, v
            v = call('control',{'action':'wait','ref':job,'wait_ms':1000})
        if success: assert v['job']['state']=='completed',v
        return v
    def bind(v): return {k:v[k] for k in ('dataset_ref','manifest_ref')}
    def ref(v): return {'result_ref':v['job']['result_ref'],'revision':v['readable_revision']}
    def query(bindings, sql, execution=None, success=True):
        return wait(call('query',{'bindings':bindings,'sql':sql,'execution':{'wait_ms':1000,**(execution or {})}}),success)
    def read(v,**kw): return call('read',{**ref(v),**kw})
    def inspect(p,success=True): return wait(call('inspect',p),success)
    def check(label): checks.append(label); print('PASS',label,flush=True)
    pid = None
    succeeded = False
    try:
        pid = call('doctor',{})['coordinator_pid']
        source = base/'source.csv'
        source.write_text('id,label,amount\n1,a,1.25\n2,b,2.50\n3,a,\n4,,4.00\n')
        schema = [{'name':'id','type':'Int64'},{'name':'label','type':'Utf8'},{'name':'amount','type':'Decimal128(20, 2)'}]
        opened = call('open',{'source':str(source),'schema':schema})
        profile = inspect({'ref':opened['manifest_ref'],'columns':['id','amount'],'checks':['null_count','min_max']})
        observed = read(profile)
        assert observed['rows'] == [['4','0','1','4','1','1.25','4.00']], observed
        assert [x['column'] for x in observed['quality']['inspection']['outputs'][1:]] == ['id']*3+['amount']*3
        assert profile['job']['metrics']['io']['source_read_bytes']==source.stat().st_size
        top = inspect({'ref':opened['manifest_ref'],'columns':['label'],'checks':['top_k'],'top_k':2})
        assert read(top)['rows']==[['a','2'],['b','1']]
        denied = inspect({'ref':opened['manifest_ref'],'columns':['amount'],'checks':['min_max'],'execution':{'scan_bytes':1}},False)
        assert denied['job']['state']=='budget_exhausted'
        check('one-scan typed null/min/max profile; deterministic top-k; scan budgets')
        saved = query({'t':bind(opened)},'SELECT * FROM t')
        prof_saved = inspect({'ref':ref(saved)['result_ref'],'revision':ref(saved)['revision'],'columns':['amount'],'checks':['null_count','min_max']})
        assert read(prof_saved)['rows']==[['4','1','1.25','4.00']]
        assert prof_saved['job']['metrics']['io']['source_read_bytes']==0
        rejected = call('inspect',{'ref':ref(saved)['result_ref'],'checks':['head']},ok=False)
        assert rejected['error']['code']=='INVALID_ARGUMENT'
        head = inspect({'ref':ref(saved)['result_ref'],'revision':ref(saved)['revision'],'checks':['head'],'budget':{'max_rows':2}})
        assert len(read(head)['rows'])==2
        check('fixed-revision result inspection avoids original reads and rejects floating row inspections')
        prepared = call('prepare',{'source':bind(opened)},key='prepare-one')
        done = wait(prepared)
        assert done['job']['prepared']==bind(prepared)
        again = call('prepare',{'source':bind(opened),'execution':{'wait_ms':0}},key='prepare-one')
        assert again['job_id']==prepared['job_id']
        replay = call('prepare',{'source':bind(opened)},key='prepare-one')
        assert replay['job_id']==prepared['job_id']
        roundtrip = query({'t':bind(prepared)},'SELECT * FROM t ORDER BY id')
        assert read(roundtrip)['rows']==[['1','a','1.25'],['2','b','2.50'],['3','a',None],['4',None,'4.00']]
        assert roundtrip['job']['metrics']['io']['source_read_bytes']==0
        assert call('inspect',{'ref':prepared['manifest_ref']})['schema_origin']=='prepared'
        source.write_text(source.read_text()+'5,c,5.50\n')
        invalid = call('query',{'bindings':{'t':bind(opened)},'sql':'SELECT * FROM t'},ok=False)
        assert invalid['error']['code']=='SOURCE_CHANGED'
        copy_still_valid = query({'t':bind(prepared)},'SELECT COUNT(*),SUM(amount) FROM t')
        assert read(copy_still_valid)['rows']==[['4','7.75']]
        check('prepare publishes typed immutable Parquet only on success; idempotency and source independence')
        empty_file=base/'empty.csv';empty_file.write_text('id,label,amount\n')
        empty=call('open',{'source':str(empty_file),'schema':schema})
        empty_prepared=call('prepare',{'source':bind(empty)});wait(empty_prepared)
        empty_sum=query({'t':bind(empty_prepared)},'SELECT COUNT(*) FROM t')
        assert read(empty_sum)['rows']==[['0']]
        late_file=base/'late.csv';late_file.write_text('x\n'+'1\n'*50000+'bad\n')
        late=call('open',{'source':str(late_file),'schema':[{'name':'x','type':'Int64'}]})
        late_prepared=call('prepare',{'source':bind(late),'execution':{'wait_ms':0}})
        late_done=wait(late_prepared,False)
        assert late_done['job']['state']=='failed' and late_done['readable_revision'] is None,late_done
        not_published=call('inspect',{'ref':late_prepared['manifest_ref']},ok=False)
        assert not_published['error']['code']=='OBJECT_NOT_FOUND'
        check('empty prepared tables retain schema; late CSV parse failure never publishes a partial dataset')
        tsv=base/'tabs.tsv';tsv.write_text('x\ty\n1\t2\n3\t4\n')
        ts=call('open',{'source':str(tsv)})
        pt=call('prepare',{'source':bind(ts)});wait(pt)
        assert read(query({'t':bind(pt)},'SELECT SUM(x+y) FROM t'))['rows']==[['10']]
        check('TSV preparation preserves delimiter semantics')
        multi=base/'multi';multi.mkdir()
        (multi/'a.csv').write_text('x,y\n1,2\n3,4\n');(multi/'b.csv').write_text('x,y\n5,6\n7,8\n')
        multi_source=call('open',{'source':str(multi),'format':'csv'})
        multi_prepared=call('prepare',{'source':bind(multi_source)});wait(multi_prepared)
        assert read(query({'t':bind(multi_prepared)},'SELECT COUNT(*),SUM(x+y) FROM t'))['rows']==[['4','36']]
        check('multi-file CSV preparation preserves all rows and per-file headers')

        # Mutate bytes while preserving inode, size and mtime: identities alone cannot detect this.
        isolated = query({},'SELECT CAST(12345 AS BIGINT) AS value')
        db = sqlite3.connect(ws/'metadata.sqlite')
        part = pathlib.Path(db.execute('SELECT path FROM parts WHERE result_id=?',(ref(isolated)['result_ref'],)).fetchone()[0])
        assert hashlib.file_digest(part.open('rb'),'sha256').hexdigest()==db.execute('SELECT checksum FROM parts WHERE path=?',(str(part),)).fetchone()[0]
        stat = part.stat(); payload=bytearray(part.read_bytes());payload[len(payload)//2]^=1;part.write_bytes(payload);os.utime(part,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        corrupt_query=query({'t':ref(isolated)},'SELECT * FROM t',success=False)
        assert corrupt_query['job']['error']['code']=='RESULT_CORRUPT',corrupt_query
        corrupt_export=wait(call('export',{**ref(isolated),'format':'parquet','destination':str(base/'corrupt.parquet')}),False)
        assert corrupt_export['job']['error']['code']=='RESULT_CORRUPT' and not (base/'corrupt.parquet').exists(),corrupt_export
        assert call('read',ref(isolated),ok=False)['error']['code']=='RESULT_CORRUPT'
        check('same-size same-mtime corruption is rejected in read, saved-result SQL and export')
        tamper_prepared=call('prepare',{'source':bind(ts)});tamper_done=wait(tamper_prepared)
        part=pathlib.Path(db.execute('SELECT path FROM parts WHERE result_id=?',(tamper_done['job']['result_ref'],)).fetchone()[0])
        external_alias=call('open',{'source':str(part)})
        stat=part.stat();payload=bytearray(part.read_bytes());payload[len(payload)//3]^=1;part.write_bytes(payload);os.utime(part,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        corrupt_alias=query({'a_managed':bind(tamper_prepared),'z_external':bind(external_alias)},'SELECT a_managed.x FROM a_managed JOIN z_external ON a_managed.x=z_external.x',success=False)
        assert corrupt_alias['job']['error']['code']=='RESULT_CORRUPT',corrupt_alias
        check('an external alias cannot downgrade integrity checks on a managed Parquet input')

        parent=query({},'SELECT CAST(99 AS BIGINT) AS x')
        child=query({'t':ref(parent)},'SELECT x+1 FROM t')
        call('control',{'action':'release','ref':ref(parent)['result_ref']})
        call('control',{'action':'pin','ref':ref(child)['result_ref']})
        call('workspace',{'action':'gc','dry_run':False})
        assert read(parent)['rows']==[['99']]
        call('control',{'action':'release','ref':ref(child)['result_ref']})
        preview=call('workspace',{'action':'gc'})
        assert preview['gc']['candidate_count']==2 and preview['gc']['dry_run']
        assert read(parent)['rows']==[['99']]
        collected=call('workspace',{'action':'gc','dry_run':False})
        assert collected['gc']['reclaimed_bytes']>0
        assert call('read',ref(parent),ok=False)['error']['code']=='OBJECT_EXPIRED'
        assert call('control',{'action':'pin','ref':ref(child)['result_ref']},ok=False)['error']['code']=='OBJECT_EXPIRED'
        assert call('workspace',{'action':'gc','dry_run':False})['gc']['candidate_count']==0
        check('pin protects ancestors; dry-run is non-destructive; GC creates durable expired-reference errors')
        # Managed datasets protect their storage while queries are retained.
        call('control',{'action':'release','ref':pt['dataset_ref']})
        assert call('workspace',{'action':'gc','dry_run':False})['gc']['candidate_count']==0
        check('retained query dependencies protect released prepared datasets')
        numbers_file=base/'numbers.csv';numbers_file.write_text('id\n'+''.join(f'{i}\n' for i in range(16384)))
        numbers=call('open',{'source':str(numbers_file)})
        material=query({'t':bind(numbers)},'SELECT id FROM t')
        active=call('query',{'bindings':{'t':ref(material)},'sql':LONG_QUERY,'execution':{'wait_ms':0}})
        call('control',{'action':'release','ref':ref(material)['result_ref']})
        call('control',{'action':'release','ref':active['result_ref']})
        assert call('control',{'action':'status','ref':active['job_id']})['job']['state'] not in terminal
        call('workspace',{'action':'gc','dry_run':False})
        assert len(read(material,max_rows=1)['rows'])==1
        call('control',{'action':'cancel','ref':active['job_id']});wait(active,False)
        call('workspace',{'action':'gc','dry_run':False})
        assert call('read',ref(material),ok=False)['error']['code']=='OBJECT_EXPIRED'
        timed_prepare=call('prepare',{'source':bind(numbers),'execution':{'run_timeout_ms':1,'wait_ms':0}})
        timed_done=wait(timed_prepare,False)
        assert timed_done['job']['state']=='budget_exhausted',timed_done
        assert call('inspect',{'ref':timed_prepare['manifest_ref']},ok=False)['error']['code']=='OBJECT_NOT_FOUND'
        check('GC protects active job inputs; stopped prepare never exposes an unfinished dataset')
        usage=call('workspace',{'action':'usage'})
        quota=usage['stored_bytes']+1024*1024
        call('workspace',{'action':'configure','quota_bytes':quota})
        rejected=call('query',{'bindings':{},'sql':'SELECT 1'},ok=False)
        assert rejected['error']['code']=='RESOURCE_EXHAUSTED'
        small=query({},'SELECT 1',execution={'result_bytes':65536,'spill_bytes':65536})
        assert read(small)['rows']==[['1']]
        assert call('workspace',{'action':'usage'})['reserved_bytes']==0
        call('workspace',{'action':'configure','quota_bytes':0})
        check('quota admission includes result and spill reservations; terminal jobs release reservations')
        # Stable join/window correctness against an independent Python reference.
        joined=query({'a':bind(prepared),'b':bind(pt)},'SELECT a.id, b.x, SUM(a.id) OVER (PARTITION BY b.x ORDER BY a.id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running FROM a JOIN b ON a.id >= b.x ORDER BY b.x,a.id')
        expected=[]
        for x in (1,3):
            total=0
            for i in range(x,5):total+=i;expected.append([str(i),str(x),str(total)])
        assert read(joined)['rows']==expected
        check('join plus partitioned window matches independent integer oracle')
        succeeded=True
    finally:
        session.stdin.close();session.wait(timeout=5)
        if pid:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        report={'status':'passed' if succeeded else 'incomplete','checks':checks,'binary_sha256':{name:hashlib.file_digest((bins/name).open('rb'),'sha256').hexdigest() for name in ['rowtrail','rowtrail-runtime']},'traces':traces}
        path=root/args.report;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':report['status'],'checks':len(checks),'report':str(path)}))
