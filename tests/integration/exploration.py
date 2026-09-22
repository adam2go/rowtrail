"""Deterministic S1/S2, lifecycle, and fault checks. No model or network required."""
from workloads import LONG_QUERY
import argparse, collections, json, os, pathlib, signal, sqlite3, subprocess, tempfile, time

parser=argparse.ArgumentParser()
parser.add_argument('--bin-dir',default='target/debug')
parser.add_argument('--report',default='benchmarks/local/integration.json')
args=parser.parse_args()
root=pathlib.Path(__file__).resolve().parents[2]
bindir=(root/args.bin_dir).resolve()
fixture=json.loads((root/'tests/fixtures/manifest.json').read_text())
temporary=tempfile.TemporaryDirectory(prefix='rowtrail-test-')
base=pathlib.Path(temporary.name)
workspace=base/'workspace'
data=base/'data'
traces=[]
checks=[]
def call(method,params,key=None,ok=True,cwd=None):
    request={'api_version':'1','request_id':f'test-{len(traces)}','method':method,'params':params}
    if key:request['idempotency_key']=key
    started=time.perf_counter()
    proc=subprocess.run([str(bindir/'rowtrail'),'--workspace',str(workspace),'call',method],input=json.dumps(request),text=True,capture_output=True,timeout=65,cwd=cwd)
    try: response=json.loads(proc.stdout)
    except Exception:raise AssertionError((proc.returncode,proc.stdout,proc.stderr))
    traces.append({'request':request,'caller_cwd':str(cwd or root),'response':response,'wall_ms':(time.perf_counter()-started)*1000,'exit':proc.returncode})
    if ok:assert response['ok'],response
    return response.get('result') if response['ok'] else response
def wait(result,timeout=30):
    job=result.get('job_id') or result['job']['id']
    stop=time.monotonic()+timeout
    while True:
        result=call('control',{'action':'wait','ref':job,'wait_ms':1000})
        if result['job']['state'] in ('completed','failed','cancelled','budget_exhausted','interrupted'):return result
        assert time.monotonic()<stop,result
def query(bindings,sql,execution=None,parameters=None,key=None,success=True):
    p={'bindings':bindings,'sql':sql,'execution':{'wait_ms':0,**(execution or {})}}
    if parameters:p['parameters']=parameters
    result=call('query',p,key=key)
    done=wait(result)
    if success:assert done['job']['state']=='completed',done
    return result,done
def binding(dataset):return {'dataset_ref':dataset['dataset_ref'],'manifest_ref':dataset['manifest_ref']}
def rb(done):return {'result_ref':done['job']['result_ref'],'revision':done['readable_revision']}
def read(done,**kwargs):return call('read',{**rb(done),**kwargs})
def check(name):checks.append(name);print('PASS',name,flush=True)
coordinator_pid=None
succeeded=False
try:
    subprocess.run([str(bindir/'rowtrail-runtime'),'fixtures','--directory',str(data),'--rows','16384'],check=True,timeout=60)
    doctor=call('doctor',{})
    coordinator_pid=doctor.get('coordinator_pid')
    csv=call('open',{'source':str(data/'small.csv'),'schema':fixture['small']['schema']},key='open-small')
    again=call('open',{'source':str(data/'small.csv'),'schema':fixture['small']['schema']},key='open-small')
    assert csv['dataset_ref']==again['dataset_ref']
    parquet=call('open',{'source':str(data/'small.parquet')})
    many=call('open',{'source':str(data/'many.parquet')})
    assert many['metadata_read_bytes']<(data/'many.parquet').stat().st_size/5
    schema=call('inspect',{'ref':csv['dataset_ref'],'columns':['id','amount'],'checks':['schema']})
    assert [c['type'] for c in schema['fields']]==['Int64','Decimal128(20, 2)']
    check('bounded open / explicit Decimal schema / idempotent open')
    directories=[]
    for name,value in [('caller-a',1),('caller-b',2)]:
        directory=base/name;directory.mkdir();directories.append(directory)
        (directory/'same.csv').write_text(f'value\n{value}\n')
        relative=call('open',{'source':'same.csv'},cwd=directory)
        _,relative_sum=query({'t':binding(relative)},'SELECT SUM(value) FROM t')
        assert read(relative_sum)['rows']==[[str(value)]]
    relative_export=wait(call('export',{**rb(relative_sum),'format':'parquet','destination':'relative.parquet'},cwd=directories[1]))
    assert relative_export['job']['state']=='completed' and (directories[1]/'relative.parquet').is_file()
    check('relative paths resolve in each caller directory, independently of coordinator cwd')

    final=None
    for source in (csv,parquet):
        _,final=query({'t':binding(source)},'SELECT region, SUM(amount) total, COUNT(*) n FROM t GROUP BY region ORDER BY region NULLS LAST')
        result=read(final)
        assert result['rows']==fixture['small']['expected_region_sum_count'],result
        assert result['quality']['accuracy']=='exact' and result['quality']['coverage']['kind']=='complete' and result['quality']['final_for_request']
    check('S1 CSV and four-row-group Parquet agree with hand-calculated results')
    _,ids=query({'t':binding(csv)},'SELECT id FROM t WHERE id >= $1 ORDER BY id',parameters=[{'type':'Int64','value':'9007199254740993'}])
    page=read(ids,max_rows=2)
    all_rows=page['rows']
    while page['next_cursor']:
        page=read(ids,max_rows=2,cursor=page['next_cursor'])
        all_rows+=page['rows']
    assert [r[0] for r in all_rows]==[str(9007199254740993+i) for i in range(8)]
    check('typed parameters / large integer preservation / fixed revision pagination')
    _,filtered=query({'t':binding(csv)},'SELECT id, region, amount FROM t WHERE amount IS NOT NULL')
    _,branch=query({'saved':rb(filtered)},'SELECT region,SUM(amount) total FROM saved GROUP BY region ORDER BY region NULLS LAST')
    assert branch['job']['metrics']['io']['source_read_bytes']==0,branch
    assert branch['job']['metrics']['io']['result_read_bytes']>0,branch
    _,branch2=query({'saved':rb(filtered)},'SELECT id FROM saved WHERE amount > 10 ORDER BY id')
    assert read(branch2)['rows']==[['9007199254740993'],['9007199254740994'],['9007199254740998']]
    assert branch['job']['metrics']['worker_pid']==branch2['job']['metrics']['worker_pid']
    assert branch2['job']['metrics']['worker_reused']
    _,missing=query({'saved':rb(filtered)},'SELECT product, COUNT(*) FROM saved GROUP BY product',success=False)
    assert missing['job']['state']=='failed'
    _,back=query({'t':binding(csv)},'SELECT product,COUNT(*) n FROM t GROUP BY product ORDER BY product')
    assert read(back)['rows']==[['A','4'],['B','4']]
    check('S2 two result-only branches avoid original reads; missing dimension requires original source')
    expected=collections.defaultdict(lambda:[0,0])
    for i in range(16384):
        region=None if i%13==0 else ['华东','华南','华北'][i%3]
        expected[region][1]+=1
        if i%17:expected[region][0]+=i%2001-1000
    _,aggregation=query({'t':binding(many)},'SELECT region,SUM(amount),COUNT(*) FROM t GROUP BY region')
    from decimal import Decimal
    actual={r[0]:[int(Decimal(r[1])*100),int(r[2])] for r in read(aggregation)['rows']}
    assert actual==dict(expected),(actual,expected)
    check('multi-row-group Decimal aggregate matches independent integer reference')
    _,packed=query({'t':binding(many)},'SELECT id FROM t ORDER BY id',execution={'preview':'none'})
    assert packed['job']['metrics']['result_parts']<=2,packed
    large_page=read(packed,max_rows=10000,max_bytes=900000)
    tail=read(packed,max_rows=10000,max_bytes=900000,cursor=large_page['next_cursor'])
    assert [r[0] for r in large_page['rows']+tail['rows']]==[str(9007199254740993+i) for i in range(16384)]
    assert not tail['next_cursor']
    _,repacked=query({'s':rb(packed)},'SELECT COUNT(*), MIN(id), MAX(id) FROM s')
    assert read(repacked)['rows']==[['16384','9007199254740993',str(9007199254740993+16383)]]
    assert repacked['job']['metrics']['io']['source_read_bytes']==0
    check('coalesced multi-batch IPC preserves 10,000-row pagination and result-only SQL')
    _,dictionary=query({'t':binding(many)},"SELECT arrow_cast(region, 'Dictionary(Int32, Utf8)') r FROM t",execution={'preview':'none'})
    _,dictionary_counts=query({'s':rb(dictionary)},'SELECT r,COUNT(*) FROM s GROUP BY r')
    assert {row[0]:int(row[1]) for row in read(dictionary_counts)['rows']}=={region:v[1] for region,v in expected.items()}
    check('changing Arrow dictionaries remain readable and reusable across engine batches')
    destination=base/'export.parquet'
    exported=wait(call('export',{**rb(final),'format':'parquet','destination':str(destination)}))
    assert exported['job']['state']=='completed',exported
    sidecar=json.loads(pathlib.Path(str(destination)+'.rowtrail.json').read_text())
    assert sidecar['quality']['final_for_request']
    reopened=call('open',{'source':str(destination)})
    _,roundtrip=query({'t':binding(reopened)},'SELECT * FROM t ORDER BY region NULLS LAST')
    assert read(roundtrip)['rows']==fixture['small']['expected_region_sum_count']
    check('export fixed revision with quality sidecar and typed roundtrip')
    request={'bindings':{'t':binding(csv)},'sql':'SELECT COUNT(*) FROM t','execution':{'wait_ms':0}}
    a=call('query',request,key='same-query');request['execution']['wait_ms']=100
    b=call('query',request,key='same-query');assert a['job_id']==b['job_id']
    request['sql']='SELECT id FROM t'
    conflict=call('query',request,key='same-query',ok=False);assert conflict['error']['code']=='IDEMPOTENCY_CONFLICT'
    check('query idempotency excludes wait but rejects changed semantics')
    for sql in ['DELETE FROM t','CREATE TABLE x AS SELECT * FROM t',"SELECT * FROM read_csv('/etc/passwd')"]:
        _,denied=query({'t':binding(csv)},sql,success=False);assert denied['job']['state']=='failed',denied
    check('read-only SQL and explicit source access enforcement')
    for column in ['SOURCE_CHANGED','RESOURCE_EXHAUSTED']:
        _,reserved=query({'t':binding(csv)},f'SELECT "{column}" FROM t',success=False)
        assert reserved['job']['state']=='failed' and reserved['job']['error']['code']=='SQL_ERROR',reserved
    _,still_valid=query({'t':binding(csv)},'SELECT COUNT(*) FROM t')
    assert read(still_valid)['rows']==[['8']]
    check('SQL identifiers cannot impersonate resource errors or invalidate valid sources')

    started=time.perf_counter()
    long=call('query',{'bindings':{'t':binding(many)},'sql':LONG_QUERY,'execution':{'wait_ms':0,'run_timeout_ms':30000}})
    assert time.perf_counter()-started<2
    deadline=time.monotonic()+5
    while True:
        status=call('control',{'action':'status','ref':long['job_id']})['job']
        assert status['state'] not in ('completed','failed','budget_exhausted','interrupted'),status
        with sqlite3.connect(workspace/'metadata.sqlite') as connection:
            worker=connection.execute('SELECT worker_pid FROM jobs WHERE id=?',(long['job_id'],)).fetchone()[0]
        if status['state']=='running' and worker:break
        assert time.monotonic()<deadline,status
        time.sleep(.005)
    call('control',{'action':'cancel','ref':long['job_id']})
    cancelled=wait(long)
    assert cancelled['job']['state']=='cancelled',cancelled
    assert cancelled['job']['metrics'].get('worker_exit_confirmed'),cancelled
    check('real background submission / external cancellation / confirmed worker exit')
    for attempt in range(8):
        streaming=call('query',{'bindings':{'t':binding(many)},'sql':'SELECT a.id FROM t a CROSS JOIN t b','execution':{'wait_ms':0,'run_timeout_ms':30000}})
        deadline=time.monotonic()+5
        while True:
            status=call('control',{'action':'status','ref':streaming['job_id']})
            state=status['job']
            if state['readable_revision']:
                assert state['state'] in ('running','stopping'),state
                break
            assert time.monotonic()<deadline,state
            time.sleep(.005)
        first=call('read',{'result_ref':state['result_ref'],'revision':state['readable_revision'],'max_rows':2})
        assert len(first['rows'])==2 and first['quality']['coverage']['kind']=='partial',first
        call('control',{'action':'cancel','ref':streaming['job_id']})
        stopped=wait(streaming)
        assert stopped['job']['state']=='cancelled',stopped
        assert stopped['job']['metrics']['worker_exit_confirmed'],stopped
        assert not stopped['job']['error'] or stopped['job']['error']['code']!='WORKER_LOST',stopped
        assert read(stopped,max_rows=2)['quality']['coverage']['kind']=='partial'
    check('first preview survives eight cancellation races during coalesced result publication')
    _,budget=query({'t':binding(many)},'SELECT * FROM t',execution={'scan_bytes':16},success=False)
    assert budget['job']['state']=='budget_exhausted',budget
    check('scan budget exhaustion does not masquerade as empty success')
    assert budget['job']['metrics']['io']['source_read_bytes']<=16
    _,timed=query({'t':binding(many)},LONG_QUERY,execution={'run_timeout_ms':30},success=False)
    assert timed['job']['state']=='budget_exhausted' and timed['job']['metrics']['worker_exit_confirmed'],timed
    check('computation timeout stops execution independently of client waiting')
    no_export=base/'budget-export.parquet'
    denied_export=wait(call('export',{**rb(final),'format':'parquet','destination':str(no_export),'execution':{'scan_bytes':1}}))
    assert denied_export['job']['state']=='budget_exhausted' and not no_export.exists(),denied_export
    assert denied_export['job']['metrics']['io']['result_read_bytes']==0
    check('export reserves input bytes before reading and does not publish on budget failure')
    bounded=call('read',{**rb(ids),'max_rows':2,'max_bytes':4096})
    encoded=json.dumps(traces[-1]['response'],ensure_ascii=False,separators=(',',':')).encode()
    assert len(encoded)<=4096 and len(bounded['rows'])==2
    too_small=call('read',{**rb(ids),'max_bytes':128},ok=False)
    assert too_small['error']['code']=='OUTPUT_BUDGET_TOO_SMALL',too_small
    inspected=call('inspect',{'ref':ids['job']['result_ref'],'checks':['schema']})
    assert inspected['revision']==ids['readable_revision'] and inspected['fields'][0]['type']=='Int64'
    check('serialized observation budgets and result schema inspection')
    malformed=call('open',{'source':str(data/'small.csv'),'invalid_'+'字'*6000:True},ok=False)
    assert malformed['error']['code']=='INVALID_ARGUMENT' and malformed['error']['details']['message_truncated']
    assert len(json.dumps(traces[-1]['response'],ensure_ascii=False,separators=(',',':')).encode())<=8192
    raw={'api_version':'1','request_id':'\0'*128,'method':'open','params':{'source':'missing.csv','output':{'max_bytes':1},'invalid':True}}
    malformed_control=subprocess.run([str(bindir/'rowtrail'),'--workspace',str(workspace),'call','open'],input=json.dumps(raw),text=True,capture_output=True,timeout=5)
    control_error=json.loads(malformed_control.stdout)
    assert not control_error['ok'] and control_error['error']['details']['request_id_truncated']
    assert len(malformed_control.stdout.encode())<=513
    check('large Unicode validation errors obey output budgets and signal truncation')


    events=call('events',{'job_ids':[filtered['job']['id']],'limit':100,'max_bytes':65536})
    duplicate=call('events',{'job_ids':[filtered['job']['id']],'limit':100,'max_bytes':65536})
    assert [x['event_id'] for x in events['events']]==[x['event_id'] for x in duplicate['events']]
    next_events=call('events',{'job_ids':[filtered['job']['id']],'after_cursor':events['next_cursor']})
    assert next_events['events']==[]
    check('durable event replay and exclusive cursor')
    snapshot=call('control',{'action':'snapshot','ref':filtered['job']['id']})
    delta=call('events',{'job_ids':[filtered['job']['id']],'after_cursor':snapshot['event_cursor']})
    assert delta['events']==[]
    check('atomic job snapshot supplies a compatible event replay cursor')

    # Termination after a completed commit must not rewrite the terminal state.
    completed_job=branch['job']['id']
    cancel_done=call('control',{'action':'cancel','ref':completed_job})
    assert cancel_done['already_terminal'] and cancel_done['job']['state']=='completed'
    # A global query has no readable revision before it has produced an answer.
    long=call('query',{'bindings':{'t':binding(many)},'sql':LONG_QUERY,'execution':{'wait_ms':0,'preview':'none'}})
    for _ in range(100):
        status=call('control',{'action':'status','ref':long['job_id']})['job']
        if status['state']=='running':break
        time.sleep(.01)
    assert status['readable_revision'] is None
    os.kill(coordinator_pid,signal.SIGKILL)
    time.sleep(.2)
    recovered=call('doctor',{})
    coordinator_pid=recovered['coordinator_pid']
    interrupted=call('control',{'action':'status','ref':long['job_id']})['job']
    assert interrupted['state']=='interrupted',interrupted
    assert read(final)['rows']==fixture['small']['expected_region_sum_count']
    check('coordinator crash isolates old attempt, preserves committed results, and reports interrupted')
    long=call('query',{'bindings':{'t':binding(many)},'sql':LONG_QUERY,'execution':{'wait_ms':0}})
    with sqlite3.connect(workspace/'metadata.sqlite') as connection:
        for _ in range(100):
            row=connection.execute('SELECT worker_pid FROM jobs WHERE id=?',(long['job_id'],)).fetchone()
            if row[0]:break
            time.sleep(.01)
        worker_pid=row[0]
    assert worker_pid
    os.kill(worker_pid,signal.SIGKILL)
    interrupted=wait(long)
    assert interrupted['job']['state']=='interrupted',interrupted
    check('worker crash becomes interrupted with confirmed process exit')
    _,damaged=query({},'SELECT CAST(42 AS BIGINT) n')
    with sqlite3.connect(workspace/'metadata.sqlite') as connection:
        from part_fixture import corrupt_part
        corrupt_part(connection, damaged['job']['result_ref'])
    corrupt=call('read',rb(damaged),ok=False)
    assert corrupt['error']['code']=='RESULT_CORRUPT',corrupt
    check('corrupt committed part fails explicitly instead of returning an empty table')
    _,empty=query({'t':binding(parquet)},'SELECT id FROM t WHERE 1=0')
    empty_read=read(empty)
    assert empty_read['rows']==[] and empty_read['schema'] and empty_read['quality']['final_for_request']
    check('genuine empty result has a schema and final committed revision')
    observed=call('inspect',{'ref':parquet['dataset_ref'],'checks':['schema','head'],'columns':['id','amount'],'budget':{'max_rows':2,'max_bytes':8192}})
    observed=wait(observed)
    assert len(read(observed)['rows'])==2
    check('bounded head inspection uses a real limited query and fixed result')
    late=data/'late.csv'
    late.write_text('id,value\n'+''.join(f'{i},{"invalid" if i==20000 else i}\n' for i in range(24576)))
    late_source=call('open',{'source':str(late),'schema':[{'name':'id','type':'Int64'},{'name':'value','type':'Int64'}]})
    _,partial=query({'t':binding(late_source)},'SELECT * FROM t',success=False)
    assert partial['job']['state']=='failed' and partial['readable_revision'],partial
    partial_read=read(partial)
    assert partial_read['quality']['coverage']['kind']=='partial' and not partial_read['quality']['final_for_request']
    _,derived=query({'t':rb(partial)},'SELECT COUNT(*) FROM t')
    derived_read=read(derived)
    assert derived_read['quality']['coverage']['kind']=='partial',derived_read
    rejected=call('export',{**rb(partial),'format':'parquet','destination':str(base/'partial.parquet')},ok=False)
    assert rejected['error']['code']=='INVALID_ARGUMENT'
    exported_partial=wait(call('export',{**rb(partial),'format':'parquet','destination':str(base/'partial.parquet'),'allow_nonfinal':True}))
    assert exported_partial['job']['state']=='completed',exported_partial
    imported=call('open',{'source':str(base/'partial.parquet')})
    _,imported_query=query({'t':binding(imported)},'SELECT COUNT(*) FROM t')
    assert read(imported_query)['quality']['coverage']['kind']=='partial'
    check('late CSV type failure preserves partial results and propagates quality through queries and export')
    _,empty_typed=query({'t':binding(late_source)},'SELECT * FROM t WHERE false')
    full_export=wait(call('export',{**rb(empty_typed),'format':'parquet','destination':str(base/'full-empty.parquet')}))
    assert full_export['job']['state']=='completed'
    import shutil
    mixed=base/'mixed';mixed.mkdir()
    shutil.copyfile(base/'full-empty.parquet',mixed/'a-full.parquet')
    shutil.copyfile(base/'partial.parquet',mixed/'b-partial.parquet')
    mixed_source=call('open',{'source':str(mixed),'format':'parquet'})
    _,mixed_result=query({'t':binding(mixed_source)},'SELECT COUNT(*) FROM t')
    assert read(mixed_result)['quality']['coverage']['kind']=='partial'
    empty_csv=base/'empty.csv'
    exported_empty=wait(call('export',{**rb(empty_typed),'format':'csv','destination':str(empty_csv)}))
    assert exported_empty['job']['state']=='completed' and empty_csv.read_text().strip()=='id,value'
    check('multi-file imports conservatively merge quality and empty CSV exports retain headers')

    # A changed original input must not be silently reused as its old version.
    with (data/'small.csv').open('a') as f:f.write('9007199254741001,2026-09-05,华东,A,1.00,new\n')
    invalid=call('query',{'bindings':{'t':binding(csv)},'sql':'SELECT COUNT(*) FROM t'},ok=False)
    assert invalid['error']['code']=='SOURCE_CHANGED',invalid
    invalid_read=call('read',rb(filtered),ok=False)
    assert invalid_read['error']['code']=='SOURCE_CHANGED',invalid_read
    check('source version mismatch and downstream invalidation')
    refreshed=call('control',{'action':'refresh','ref':csv['dataset_ref']})
    assert refreshed['dataset_ref']==csv['dataset_ref'] and refreshed['manifest_ref']!=csv['manifest_ref']
    _,fresh=query({'t':binding(refreshed)},'SELECT COUNT(*) FROM t')
    assert read(fresh)['rows']==[['9']]
    check('refresh freezes a new manifest while preserving old result invalidation')
    succeeded=True
    report={'checks':checks,'traces':traces,'fixture':fixture,'status':'passed'}
    path=root/args.report;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'status':'passed','checks':len(checks),'report':str(path)}))
finally:
    if coordinator_pid:
        try:os.kill(coordinator_pid,signal.SIGTERM)
        except ProcessLookupError:pass
    # Save failures too; no cherry-picking successful trajectories.
    if not succeeded:
        path=root/args.report;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'status':'incomplete','checks':checks,'traces':traces},ensure_ascii=False,indent=2))
    temporary.cleanup()
