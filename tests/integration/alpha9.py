"""Numeric correctness, stable endpoint discovery and bounded stdlib composition."""
import argparse, concurrent.futures, hashlib, json, os, pathlib, signal, sqlite3
import subprocess, sys, tempfile, time
from decimal import Decimal
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail,RowTrailError,JobNotCompleted,PageBudgetExceeded
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--bin-dir',default='target/release');p.add_argument('--report',default='benchmarks/local/alpha9.json')
a=p.parse_args();bins=(ROOT/a.bin_dir).resolve();checks=[];traces=[];pids=set();passed=False

def check(name):checks.append(name);print('PASS',name,flush=True)
def reject(fn,code):
    try:fn()
    except RowTrailError as e:
        assert e.code==code,(code,e,e.response)
        return e
    raise AssertionError('expected '+code)
class Logged(RowTrail):
    def call(self,method,params,idempotency_key=None):
        try:
            value=super().call(method,params,idempotency_key)
            traces.append({'method':method,'params':params,'result':value});return value
        except RowTrailError as e:
            traces.append({'method':method,'params':params,'error':{'code':e.code,'details':e.details},'response':e.response});raise

def stop(pid):
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:return
    until=time.monotonic()+5
    while time.monotonic()<until:
        state=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
        if not state or state.startswith('Z'):return
        time.sleep(.01)
    raise AssertionError('coordinator did not stop')

with tempfile.TemporaryDirectory(prefix='rowtrail-alpha9-') as td:
    base=pathlib.Path(td);ws=base/'workspace';rt=None
    try:
        rt=Logged(str(bins/'rowtrail'),str(ws))
        hello=rt.call('doctor',{});pid=hello['coordinator_pid'];pids.add(pid)
        assert 'numeric' in hello['capabilities']
        assert sqlite3.connect(ws/'metadata.sqlite').execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='9'
        for target in (1,2):
            for typ,values in [('BIGINT', ['9223372036854775807','1']),('BIGINT',['-9223372036854775808','-1']),
                               ('BIGINT UNSIGNED',['18446744073709551615','1']),('DECIMAL(38,0)',['9'*38,'1'])]:
                sql='SELECT SUM(n) FROM (VALUES '+','.join(f"(CAST('{v}' AS {typ}))" for v in values)+') t(n)'
                e=reject(lambda:rt.query(sql,execution={'target_partitions':target}), 'ARITHMETIC_OVERFLOW')
                assert isinstance(e,JobNotCompleted) and e.response['job']['state']=='failed'
                assert e.details['operation']=='SUM' and e.details['data_type'] and not e.retryable
                assert e.response['readable_revision'] is None
        check('signed, unsigned and Decimal SUM overflow fails durably with structured details and no readable bad revision')
        rows=rt.query("SELECT SUM(n) s FROM (VALUES ('9223372036854775807'::DECIMAL(38,0)),(1::DECIMAL(38,0))) t(n)")
        assert rt.rows(rows,numeric_policy='rowtrail-numeric-v1')==[['9223372036854775808']]
        assert rt.typed_rows(rows)==[[Decimal(9223372036854775808)]]
        assert rt.rows(rt.query("SELECT SUM(n) FROM (VALUES ('9223372036854775807'::BIGINT),(1::BIGINT),(-1::BIGINT)) t(n)"))==[['9223372036854775807']]
        assert rt.rows(rt.query('SELECT SUM(n) FROM (VALUES (NULL::BIGINT)) t(n)'))==[[None]]
        interval=rt.query("SELECT INTERVAL '1 day' elapsed")
        assert rt.typed_rows(interval)==rt.rows(interval)
        check('wider Decimal, cancellation within a wide sum, nulls, explicit policy and lossless Python values')
        for sql in [
            "SELECT g,SUM(n) FROM (VALUES (1,'9223372036854775807'::BIGINT),(1,1::BIGINT)) t(g,n) GROUP BY g",
            "SELECT SUM(DISTINCT n) FROM (VALUES ('9223372036854775807'::BIGINT),(1::BIGINT),(1::BIGINT)) t(n)",
            "SELECT SUM(n) OVER (ORDER BY i ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM (VALUES (1,'9223372036854775807'::BIGINT),(2,1::BIGINT)) t(i,n)"]:
            reject(lambda:rt.query(sql),'ARITHMETIC_OVERFLOW')
        good=rt.query('SELECT i,SUM(n) OVER (ORDER BY i ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM (VALUES (1,2::BIGINT),(2,3::BIGINT),(3,4::BIGINT)) t(i,n) ORDER BY i')
        assert rt.typed_rows(good)==[[1,2],[2,5],[3,7]]
        distinct=rt.query('SELECT i,SUM(DISTINCT n) OVER (ORDER BY i ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM (VALUES (1,2::BIGINT),(2,2::BIGINT),(3,3::BIGINT),(4,3::BIGINT)) t(i,n) ORDER BY i')
        assert rt.typed_rows(distinct)==[[1,2],[2,2],[3,5],[4,3]]
        check('grouped, DISTINCT and sliding-window overflow with successful retraction answers')
        invalid="SELECT CAST('"+'9'*38+"' AS DECIMAL(38,0))+CAST(1 AS DECIMAL(38,0)) AS bad"
        e=reject(lambda:rt.query(invalid),'ARITHMETIC_OVERFLOW')
        assert e.details['operation']=='decimal_output' and e.response['readable_revision'] is None
        check('a scalar Decimal exceeding declared precision is rejected before Arrow publication or display')

        csv=base/'orders.csv';csv.write_text('id,amount,region\n1,10,east\n2,20,west\n3,,east\n4,40,east\n',encoding='utf-8')
        opened=rt.open(csv,label='orders')
        schema=rt.inspect(opened)
        assert schema['field_count']==3
        prepared=rt.prepare(opened)
        assert 'dataset_ref' in rt.binding(prepared)
        profile=rt.inspect(prepared,checks=('null_count','min_max'),columns=('amount',))
        assert rt.typed_rows(profile)==[[4,1,10,40]]
        saved=rt.query('SELECT * FROM t ORDER BY id',{'t':prepared},label='handoff',execution={'output':{'max_rows':0}})
        out=base/'saved.parquet';exported=rt.export(saved,out)
        assert exported['job']['state']=='completed' and out.exists()
        reopened=rt.open(out)
        total=rt.query('SELECT SUM(amount) FROM t',{'t':reopened})
        assert rt.typed_rows(total)==[[70]]
        assert total['observation']['quality']['lineage'][0]['numeric']['policy']=='rowtrail-numeric-v1'
        check('prepare, fixed-manifest inspect and streaming export compose; Parquet reopening preserves numeric provenance')
        item=rt.find('handoff');assert rt.binding(item)==rt.binding(saved)
        reject(lambda:rt.find('missing'),'LABEL_NOT_FOUND')
        rt.query('SELECT 1',label='duplicate');rt.query('SELECT 2',label='duplicate')
        reject(lambda:rt.find('duplicate'),'LABEL_AMBIGUOUS')
        expired=rt.query('SELECT 1',label='expired')
        rt.call('control',{'action':'release','ref':rt.binding(expired)['result_ref']});rt.call('workspace',{'action':'gc','dry_run':False})
        reject(lambda:rt.find('expired'),'RESULT_UNAVAILABLE')
        check('strict label lookup returns a unique fixed binding and rejects missing, ambiguous and expired matches')
        pages=list(rt.pages(saved,max_rows=4,max_bytes=32768,max_pages=4,page_rows=1,page_bytes=8192))
        assert [row[0] for page in pages for row in page['rows']]==['1','2','3','4']
        cursor=None
        try:
            list(rt.pages(saved,max_rows=2,max_bytes=32768,max_pages=4,page_rows=1,page_bytes=8192))
            raise AssertionError('truncation silently treated as EOF')
        except PageBudgetExceeded as e:
            assert e.details['rows']==2 and e.details['pages']==2 and e.details['wire_bytes']<=32768
            cursor=e.details['next_cursor'];assert cursor
        rest=list(rt.pages(saved,max_rows=2,max_bytes=32768,max_pages=4,page_rows=1,page_bytes=8192,cursor=cursor))
        assert [r[0] for page in rest for r in page['rows']]==['3','4']
        reject(lambda:list(rt.pages(saved,max_rows=4,max_bytes=32768,max_pages=1,page_rows=1)),'PAGE_BUDGET_EXHAUSTED')
        reject(lambda:list(rt.pages(saved,max_rows=4,max_bytes=1000,max_pages=4)),'PAGE_BUDGET_EXHAUSTED')
        check('total pagination row/page/wire-byte bounds distinguish EOF from exhaustion and resume the exact cursor')

        # A saved result larger than the verified cache must still fit a
        # one-pass scan budget. This failed under byte-range repartitioning.
        n=1048576
        subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',str(base/'large'),'--rows',str(n)],check=True,stdout=subprocess.DEVNULL)
        large=rt.open(base/'large/many.parquet')
        large_saved=rt.query('SELECT id,region,amount,payload FROM t',{'t':large},execution={'output':{'max_rows':0}})
        with sqlite3.connect(ws/'metadata.sqlite') as db:
            sizes=[row[0] for row in db.execute('SELECT bytes FROM parts WHERE result_id=?',[rt.binding(large_saved)['result_ref']])]
        size=sum(sizes);assert size>8*1024*1024 and max(sizes)<=8*1024*1024
        for _ in range(2):
            one_pass=rt.query('SELECT COUNT(*),SUM(CAST(id AS DECIMAL(38,0))) FROM t',{'t':large_saved},execution={'target_partitions':2,'scan_bytes':size})
            assert rt.rows(one_pass)==[[str(n),str(n*9007199254740993+n*(n-1)//2)]]
            io=one_pass['job']['metrics']['io']
            assert io['result_read_bytes']==size and io['source_read_bytes']==0 and io['verified_cache_peak_bytes']<=8*1024*1024
        check('large multi-part results scan once per job under an exact byte budget; new jobs reverify with the same 8 MiB cache')

        # Old metadata is unknown, not recertified during reading or downstream work.
        legacy=rt.query('SELECT 7 n');ref=rt.binding(legacy)
        with sqlite3.connect(ws/'metadata.sqlite') as db:
            db.execute("UPDATE revisions SET quality=json_remove(quality,'$.numeric') WHERE result_id=?",[ref['result_ref']])
        page=rt.call('read',ref);assert 'numeric' not in page['quality']
        response={**legacy,'observation':page}
        try:rt.rows(response,numeric_policy='rowtrail-numeric-v1');raise AssertionError('recertified legacy result')
        except ValueError:pass
        derived=rt.query('SELECT SUM(n) FROM t',{'t':ref})
        assert derived['observation']['quality']['numeric']['input_provenance']=='unknown'
        assert derived['observation']['quality']['lineage'][0]['numeric']['policy']=='unknown'
        for _ in range(3):
            derived=rt.query('SELECT * FROM t',{'t':derived})
            assert derived['observation']['quality']['numeric']['input_provenance']=='unknown'
        check('legacy quality remains unknown in client policy checks and derived numeric lineage')

        rt.__exit__();rt=None
        previous=os.environ.get('TMPDIR')
        try:
            for name in ('tmp A','tmp-B'):
                folder=base/name;folder.mkdir();os.environ['TMPDIR']=str(folder)
                with Logged(str(bins/'rowtrail'),str(ws)) as changed:
                    assert changed.call('doctor',{})['coordinator_pid']==pid
                    assert changed.binding(changed.find('handoff'))==RowTrail.binding(saved)
        finally:
            if previous is None:os.environ.pop('TMPDIR',None)
            else:os.environ['TMPDIR']=previous
        check('TMPDIR changes reconnect to the same live coordinator and saved fixed revision')
        descriptor=ws/'runtime-endpoint.json';original=descriptor.read_text();data=json.loads(original)
        for field,value in [('pid',0),('workspace',str(base/'wrong')),('runtime_version','not-the-live-version')]:
            corrupt={**data,field:value};descriptor.write_text(json.dumps(corrupt))
            with Logged(str(bins/'rowtrail'),str(ws)) as invalid:
                e=reject(lambda:invalid.call('doctor',{}),'ENDPOINT_INVALID')
                assert e.details['session_unusable'] and not invalid.usable
            descriptor.write_text(original)
        descriptor.chmod(0o644)
        with Logged(str(bins/'rowtrail'),str(ws)) as invalid:
            reject(lambda:invalid.call('doctor',{}),'ENDPOINT_INVALID')
        descriptor.chmod(0o600)
        check('descriptor ownership/privacy and live workspace/PID checks reject mismatches with actionable structured errors')
        # A killed coordinator leaves a valid descriptor; a lock-owning successor replaces it.
        os.kill(pid,signal.SIGKILL);pids.discard(pid)
        for _ in range(200):
            state=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
            if not state or state.startswith('Z'):break
            time.sleep(.01)
        with Logged(str(bins/'rowtrail'),str(ws)) as fresh:
            new_pid=fresh.call('doctor',{})['coordinator_pid'];pids.add(new_pid);assert new_pid!=pid
            assert fresh.binding(fresh.find('handoff'))==RowTrail.binding(saved)
        check('stale descriptor/socket after coordinator death is replaced under the workspace lock without losing fixed results')
        long_ws=base/('long-'*35)/'workspace';long_ws.mkdir(parents=True,mode=0o700)
        def doctor(_):
            result=subprocess.run([str(bins/'rowtrail'),'--workspace',str(long_ws),'doctor'],capture_output=True,text=True,timeout=15)
            value=json.loads(result.stdout);assert result.returncode==0 and value['ok'],value
            return value['result']['coordinator_pid']
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            found=list(pool.map(doctor,range(8)))
        assert len(set(found))==1;pids.update(found)
        check('simultaneous cold clients and long workspace paths converge on one coordinator')
        previous=os.environ.get('ROWTRAIL_RUNTIME');os.environ['ROWTRAIL_RUNTIME']=str(base/'missing-runtime')
        try:
            with Logged(str(bins/'rowtrail'),str(base/'missing')) as missing:
                e=reject(lambda:missing.call('doctor',{}),'RUNTIME_START_FAILED')
                assert not e.retryable and 'runtime_log' in e.details
        finally:
            if previous is None:os.environ.pop('ROWTRAIL_RUNTIME',None)
            else:os.environ['ROWTRAIL_RUNTIME']=previous
        fake=RowTrail.__new__(RowTrail);fake.usable=True;fake.response_mode="full"
        fake.process=subprocess.Popen([sys.executable,'-u','-c',
            "import sys,json,time; r=json.loads(sys.stdin.readline()); print(json.dumps({'api_version':'1','request_id':r['request_id'],'ok':False,'error':'malformed'}),flush=True); time.sleep(30)"],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:fake.call('doctor',{});raise AssertionError('accepted malformed error envelope')
        except ConnectionError:assert not fake.usable and fake.process.poll() is not None
        check('failed runtime startup retains code, retryability and log path through the Python NDJSON client')
        passed=True
    finally:
        if rt:rt.__exit__()
        for pid in pids:stop(pid)
        def scrub(v):
            if isinstance(v,str):return v.replace(str(base),'<fixture>').replace(str(base).lstrip('/'),'<fixture>')
            if isinstance(v,list):return [scrub(x) for x in v]
            if isinstance(v,dict):return {k:scrub(x) for k,x in v.items()}
            return v
        report={'status':'passed' if passed else 'incomplete','checks':checks,
            'binary_sha256':{name:hashlib.file_digest((bins/name).open('rb'),'sha256').hexdigest() for name in ('rowtrail','rowtrail-runtime')},'traces':scrub(traces)}
        out=ROOT/a.report;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':report['status'],'checks':len(checks)}))
