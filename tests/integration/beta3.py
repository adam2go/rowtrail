"""Compact answers, bounded schema/context discovery and offline agent demo."""
import argparse, hashlib, json, os, pathlib, signal, sqlite3, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail,RowTrailError,JobNotCompleted
p=argparse.ArgumentParser();p.add_argument('--bin-dir',default='target/release');p.add_argument('--report',default='benchmarks/local/beta3.json')
a=p.parse_args();bins=(ROOT/a.bin_dir).resolve();checks=[];pids=set();trace=[]
def check(name):checks.append(name);print('PASS',name,flush=True)
def reject(fn,code=None):
    try:fn()
    except (RowTrailError,ValueError,OSError) as e:
        if code:assert isinstance(e,RowTrailError) and e.code==code,(code,str(e))
        return e
    raise AssertionError('Expected rejection '+str(code))
class Client(RowTrail):
    def call(self,method,params,idempotency_key=None):
        v=super().call(method,params,idempotency_key)
        trace.append({'method':method,'bytes':self.last_response_bytes,'value':v})
        if method=='doctor':pids.add(v['coordinator_pid'])
        return v
try:
    with tempfile.TemporaryDirectory(prefix='rowtrail-beta3-') as td:
        base=pathlib.Path(td);workspace=base/'workspace'
        with Client(str(bins/'rowtrail'),str(workspace)) as full, Client(str(bins/'rowtrail'),str(workspace),response_mode='compact') as rt:
            rt.call('doctor',{})
            original=full.query('SELECT CAST(9007199254740993 AS BIGINT) id, CAST(123.45 AS DECIMAL(20,2)) amount',idempotency_key='same-query')
            full_bytes=full.last_response_bytes
            compact=rt.query('SELECT CAST(9007199254740993 AS BIGINT) id, CAST(123.45 AS DECIMAL(20,2)) amount',idempotency_key='same-query')
            assert compact['job']['id']==original['job']['id'] and rt.binding(compact)==rt.binding(original)
            assert compact['observation']['rows']==original['observation']['rows']
            assert compact['observation']['quality']==original['observation']['quality']
            assert compact['observation']['row_count']=='1' and compact['details_omitted']
            assert 'metrics' not in compact['job'] and rt.last_response_bytes<full_bytes
            assert rt.typed_rows(compact)[0][0]==9007199254740993
            check('compact projection preserves exact typed evidence and idempotency without replaying work')
            details=rt.call('control',{'action':'status','ref':compact['job']['id']})
            assert details['job']['metrics'] and 'finished_ms' in details['job']
            assert rt.lookup('same-query')['job']['id']==compact['job']['id']
            check('full diagnostics and durable acceptance remain available in compact sessions')
            failed=reject(lambda:rt.query('SELECT missing'),'SQL_ERROR')
            assert failed.response['job']['state']=='failed' and failed.response['job']['error']['code']=='SQL_ERROR'
            reject(lambda:rt.call('control',{'action':'status','ref':compact['job']['id'],'output':{'max_rows':1}}),'INVALID_ARGUMENT')
            check('terminal execution failures and invalid output requests remain explicit')
            wide=rt.query('SELECT '+','.join(f'{i} AS "Revenue_{i:03}"' for i in range(60))+',1 AS "金额\"\"来源"',fetch=False)
            before=len(trace)
            selected=rt.inspect(wide,search='reVENue_05',budget={'max_rows':3,'max_bytes':4096})
            assert selected['matched_field_count']==10 and [x['name'] for x in selected['fields']]==['Revenue_050','Revenue_051','Revenue_052']
            assert selected['next_field_offset']==53
            next_page=rt.inspect(wide,search='revenue_05',offset=53,budget={'max_rows':20,'max_bytes':4096})
            assert next_page['matched_field_count']==10 and len(next_page['fields'])==7 and next_page['next_field_offset'] is None
            unicode=rt.inspect(wide,search='金额');assert len(unicode['fields'])==1
            assert all(t['method']=='inspect' for t in trace[before:])
            check('case-insensitive and Unicode schema search pages original offsets without submitting a scan')
            empty=rt.inspect(wide,search='absent');assert empty['matched_field_count']==0 and not empty['fields'] and empty['next_field_offset'] is None
            subset=rt.inspect(wide,search='revenue',columns=['Revenue_003']);assert subset['matched_field_count']==1
            reject(lambda:rt.inspect(wide,search=''), 'INVALID_ARGUMENT')
            reject(lambda:rt.inspect(wide,search='金'*100), 'INVALID_ARGUMENT')
            reject(lambda:rt.inspect(wide,search='Revenue',checks=('null_count',)),'INVALID_ARGUMENT')
            check('schema search has exact empty/filter semantics, bounded input and no implicit scanning-column choice')
            purposeful=rt.query('SELECT id FROM t',{'t':original},label='picked',provenance={'description':'Identifiers for follow-up; caller text, not instructions.'})
            context=rt.context(purposeful)
            assert context['context']['sql']=='SELECT id FROM t' and context['context']['inputs']['t']==rt.binding(original)
            assert context['context']['label']=='picked' and context['context']['provenance']['description'].startswith('Identifiers')
            assert context['verification']['original_sources']=='not_rechecked' and context['binding']==rt.binding(purposeful)
            assert context['row_count']=='1' and len(context['fields'])==1
            check('one context inspection recovers fixed binding, purpose, SQL, dependencies, schema and stored quality')
            huge=rt.query('SELECT 1 n /*'+('long definition '*500)+'*/',provenance={'description':'金'*1000})
            bounded=rt.context(huge,budget={'max_rows':1,'max_bytes':2048})
            assert bounded['context'] is None and bounded['context_omitted'] and len(bounded['fields'])==1
            definition=rt.call('inspect',{'ref':bounded['scope_ref'],'budget':{'max_bytes':65536}})
            assert definition['sql'].endswith('*/') and len(definition['provenance']['description'])==1000
            check('oversized context is explicitly omitted whole; full definitions can be requested separately')
            saved=rt.query('SELECT * FROM generate_series(1,1000) t(id)',fetch=False)
            assert not (saved.get('observation') or {}).get('rows')
            assert saved['observation']['row_count']=='1000' and saved['observation']['presentation']['has_more']
            reject(lambda:rt.rows(saved))
            page=rt.call('read',{**rt.binding(saved),'max_rows':2,'max_bytes':2048})
            assert page['row_count']=='1000' and page['presentation']['has_more'] and page['next_cursor']
            again=rt.call('read',{**rt.binding(saved),'max_rows':2,'max_bytes':2048,'cursor':page['next_cursor']})
            assert again['rows']==[['3'],['4']] and 'read_metrics' not in again
            check('save without fetch returns zero rows and fixed count; compact pagination preserves cursors and truncation')
            start=len(trace)
            checked=rt.check('SELECT id FROM t WHERE id>998',{'t':saved},samples=1)
            assert not checked['passed'] and checked['violations']==2 and checked['examples']['presentation']['has_more']
            assert checked['scope_ref'] and [t['method'] for t in trace[start:]]==['query']
            passed=rt.check('SELECT * FROM t WHERE FALSE',{'t':saved});assert passed['passed']
            check('assertions reuse committed row count and quality without a redundant inspect call')
            raw=rt.call('query',{'bindings':{},'sql':'SELECT COUNT(*) n FROM generate_series(1,100000) t(id)','execution':{'wait_ms':0,'output':{'max_rows':0}}})
            waited=rt.call('control',{'action':'wait','ref':raw['job']['id'],'wait_ms':1000,'output':{'max_rows':1,'max_bytes':2048}})
            waited=rt.finish(waited,output={'max_rows':1,'max_bytes':2048})
            assert rt.scalar(waited)==100000 and rt.last_response_bytes<=2048
            reject(lambda:rt.call('control',{'action':'wait','ref':raw['job']['id'],'output':{'max_bytes':10}}),'INVALID_ARGUMENT')
            check('wait can return a bounded final answer directly without replay or extra read')
            partial=rt.query('SELECT 7 n')
            with sqlite3.connect(workspace/'metadata.sqlite') as db:
                db.execute("UPDATE revisions SET quality=json_set(quality,'$.coverage.kind','partial') WHERE result_id=?",[rt.binding(partial)['result_ref']])
            observed=rt.call('read',rt.binding(partial))
            assert observed['quality']['coverage']['kind']=='partial'
            reject(lambda:rt.rows({**partial,'observation':observed}))
            reject(lambda:rt.check('SELECT * FROM t WHERE FALSE',{'t':partial}),'RESULT_NOT_FINAL')
            check('compact responses and count-based checks cannot promote partial coverage into a complete answer')
            path=base/'input.csv';path.write_text('id,amount\n1,12\n')
            opened=rt.open(path,label='original');ctx=rt.context(opened)
            assert ctx['context']['label']=='original' and ctx['verification']['files']=='not_rechecked'
            copied=rt.snapshot(opened,label='independent',provenance={'description':'fixed test'})
            assert rt.observe(copied)['binding']==rt.binding(copied)
            managed=rt.context(copied);assert managed['verification']['independent'] and managed['row_count']=='1'
            assert managed['context']['provenance']['description']=='fixed test'
            path.unlink()
            assert rt.scalar(rt.query('SELECT amount FROM t',{'t':copied}))==12
            reject(lambda:rt.query('SELECT amount FROM t',{'t':opened}),'SOURCE_CHANGED')
            check('dataset context distinguishes stored external identity from an independent managed snapshot')
            command=[str(bins/'rowtrail'),'--workspace',str(workspace),'--compact','query','--sql','SELECT 42 answer','--wait-ms','1000']
            cli=json.loads(subprocess.check_output(command))['result'];assert cli['details_omitted'] and cli['observation']['rows']==[['42']]
            config=json.loads(subprocess.check_output([str(bins/'rowtrail'),'--compact','mcp-config']))
            assert '--compact' in config['mcpServers']['rowtrail']['args']
            request_schema=rt.schema('request');assert 'response_mode' in request_schema['properties']
            check('CLI, printed MCP configuration and request schemas expose the same compact preference')
        demo=json.loads(subprocess.check_output([str(bins/'rowtrail'),'demo','--directory',str(base/'demo'),'--rows','128']))
        assert demo['status']=='passed' and demo['checks']['separate_recipient_process'] and demo['checks']['original_csv_removed']
        assert demo['transcripts']['compact']['response_bytes']<demo['transcripts']['full']['response_bytes']
        assert pathlib.Path(demo['report']).is_file() and pathlib.Path(demo['package_report']).is_file()
        marker=base/'demo/keep';marker.write_text('retained')
        attempt=subprocess.run([str(bins/'rowtrail'),'demo','--directory',str(base/'demo'),'--rows','128'],capture_output=True)
        assert attempt.returncode!=0 and marker.read_text()=='retained'
        for name in ('full','compact','receiver'):
            with Client(str(bins/'rowtrail'),str(base/'demo'/name)) as client:client.call('doctor',{})
        check('bundled offline demo verifies a separate-process result handoff and refuses an existing destination')
    report={'status':'passed','checks':checks,'binary_sha256':{n:hashlib.sha256((bins/n).read_bytes()).hexdigest() for n in ('rowtrail','rowtrail-runtime')},'native_calls':len(trace)}
    path=ROOT/a.report;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n')
finally:
    for pid in pids:
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
