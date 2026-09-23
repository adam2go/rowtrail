"""Snapshot, assertion, diff, recipe and portable-branch acceptance tests."""
import argparse, hashlib, json, os, pathlib, signal, sqlite3, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail,RowTrailError,JobNotCompleted
p=argparse.ArgumentParser();p.add_argument('--bin-dir',default='target/release');p.add_argument('--report',default='benchmarks/local/beta2.json')
a=p.parse_args();bins=(ROOT/a.bin_dir).resolve();checks=[];pids=set();traces=[]
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
        traces.append({'method':method,'bytes':self.last_response_bytes})
        if method=='doctor':pids.add(v['coordinator_pid'])
        return v
try:
    with tempfile.TemporaryDirectory(prefix='rowtrail-beta2-') as td:
        base=pathlib.Path(td)
        with Client(str(bins/'rowtrail'),str(base/'workspace')) as rt:
            rt.call('doctor',{})
            initial=rt.query("SELECT * FROM (VALUES (1,'Organic',10),(2,'paid',20),(3,'organic',30)) t(id,channel,amount)")
            parquet=base/'clean.parquet';rt.export(initial,parquet)
            opened=rt.open(parquet)
            provenance={'origin':'clean.py:12','code':'frame.write_parquet(path)','description':'Cleaned sessions; channel is not normalized yet.'}
            snapshot=rt.snapshot(opened,label='sessions',provenance=provenance,idempotency_key='snapshot-once')
            again=rt.snapshot(opened,label='sessions',provenance=provenance,idempotency_key='snapshot-once')
            assert rt.binding(snapshot)==rt.binding(again)
            assert rt.binding(rt.find('sessions',kind='dataset'))==rt.binding(snapshot)
            meta=rt.inspect(snapshot,checks=('provenance',));assert meta['provenance']==provenance and meta['verification']['independent']
            assert len(meta['identity']['files'])>0
            reject(lambda:rt.snapshot(opened,label='different',idempotency_key='snapshot-once'),'IDEMPOTENCY_CONFLICT')
            reject(lambda:rt.snapshot(opened,provenance={'code':'x'*16385}),'INVALID_ARGUMENT')
            check('Parquet snapshot is labeled, provenance-bearing, idempotent and bounded')
            dependent=rt.query('SELECT * FROM t',{'t':opened})
            independent=rt.snapshot(dependent,label='fixed copy')
            parquet.unlink()
            reject(lambda:rt.query('SELECT * FROM t',{'t':opened}),'SOURCE_CHANGED')
            reject(lambda:rt.call('read',rt.binding(dependent)),'SOURCE_CHANGED')
            assert rt.scalar(rt.query('SELECT SUM(amount) FROM t',{'t':snapshot}))==60
            assert rt.scalar(rt.query('SELECT COUNT(*) FROM t',{'t':independent}))==3
            check('managed dataset and result snapshots survive deletion and original dependency invalidation')
            start=len(traces)
            small=rt.query('SELECT 42 answer',execution={'output':{'max_rows':1,'max_bytes':2048}})
            assert rt.scalar(small)==42 and len(traces)==start+1,traces[start:]
            assert traces[-1]['bytes']<=2048 and small['observation']['quality']['final_for_request']
            reject(lambda:rt.scalar(initial))
            assert rt.records(small)==[{'answer':42}]
            check('2 KiB scalar answer stays in one call with quality intact and guarded typed access')
            before=rt.query('SELECT * FROM t',{'t':snapshot},provenance={'description':'Before normalization'},label='baseline')
            after=rt.query("SELECT id,lower(channel) channel,amount+CASE WHEN id=2 THEN 5 ELSE 0 END amount FROM t WHERE id<>3 UNION ALL SELECT 4,'referral',40",{'t':snapshot})
            delta=rt.diff(before,after,keys=['id'],samples=2)
            assert delta['status']=='compared' and delta['rows']=={'before':3,'after':3,'added':1,'deleted':1,'modified':2},delta
            assert delta['changed_fields']=={'channel':1,'amount':1} and delta['samples_limited']
            assert len(delta['examples']['rows'])==2
            check('keyed diff counts additions, removals and per-field changes with bounded examples')
            dupe=rt.query('SELECT id,channel,amount FROM t UNION ALL SELECT * FROM t',{'t':before})
            blocked=rt.diff(before,dupe,keys=['id']);assert blocked['status']=='blocked' and blocked['key_checks']['after']['duplicate_keys']==3
            null=rt.query("SELECT NULL::BIGINT id,'organic' channel,10 amount")
            assert rt.diff(before,null,keys=['id'])['status']=='blocked'
            changed=rt.query('SELECT id,amount,1 extra FROM t',{'t':before})
            d=rt.diff(before,changed,keys=['id']);assert d['schema']=={'added':['extra'],'removed':['channel'],'changed':{}}
            assert d['comparison_columns']==['amount']
            check('duplicate and null keys block comparison; schema changes remain separate from value changes')
            passed=rt.check('SELECT id,COUNT(*) n FROM t GROUP BY id HAVING COUNT(*)>1',{'t':before},name='unique ids')
            failed=rt.check('SELECT * FROM t WHERE amount>15',{'t':before},samples=1,name='amount ceiling')
            assert passed['passed'] and passed['violations']==0
            assert not failed['passed'] and failed['violations']==2 and failed['examples']['presentation']['has_more']
            assert failed['quality']['coverage']['kind']=='complete'
            reject(lambda:rt.check('SELECT missing FROM t',{'t':before}),'SQL_ERROR')
            forbidden=rt.check(bindings={'t':before},forbidden_columns=['channel'],name='no leakage')
            assert not forbidden['passed'] and forbidden['violations']==1 and forbidden['examples']['rows']==[['channel']]
            clean=rt.check(bindings={'t':before},forbidden_columns=['target']);assert clean['passed']
            check('reusable SQL assertions retain exact violation counts and bounded evidence; SQL failure cannot pass')
            # A failed/partial legacy revision must not be promoted into a passing
            # assertion, comparable full version or independently certified copy.
            partial=rt.query('SELECT 1 id')
            with sqlite3.connect(base/'workspace/metadata.sqlite') as db:
                db.execute("UPDATE revisions SET quality=json_set(quality,'$.coverage.kind','partial') WHERE result_id=?",[rt.binding(partial)['result_ref']])
            reject(lambda:rt.snapshot(partial),'RESULT_NOT_FINAL')
            reject(lambda:rt.diff(partial,before,keys=['id']),'RESULT_NOT_FINAL')
            reject(lambda:rt.check('SELECT * FROM t WHERE FALSE',{'t':partial}),'RESULT_NOT_FINAL')
            check('partial coverage cannot pass checks, diffs or independent snapshot admission')
            recipe={'format':'rowtrail.recipe.v1','inputs':['sessions'],
                'parameters':{'minimum':{'type':'Int64','value':'15'}},'steps':[
                {'id':'clean','sql':'SELECT id,lower(channel) channel,amount FROM t WHERE amount >= $1',
                 'bindings':{'t':'input:sessions'},'parameters':['minimum'],'description':'Normalize qualifying sessions'},
                {'id':'unique_ids','kind':'check','sql':'SELECT id FROM t GROUP BY id HAVING COUNT(*)>1','bindings':{'t':'step:clean'}},
                {'id':'total','sql':'SELECT SUM(amount) total FROM t','bindings':{'t':'step:clean'}}]}
            first=rt.run_recipe(recipe,{'sessions':before},run_dir=base/'run1')
            assert first['status']=='completed' and len(first['steps'])==3
            assert first['steps'][1]['passed']
            second=rt.run_recipe(recipe,{'sessions':after},parameters={'minimum':{'type':'Int64','value':'20'}},run_dir=base/'run2')
            assert second['run_id']!=first['run_id'] and second['inputs']!=first['inputs']
            value=rt.query('SELECT * FROM t',{'t':second['steps'][-1]['binding']});assert rt.scalar(value)==65
            assert json.loads((base/'run2/run.json').read_text())==second
            assert all(s['idempotency_key'].startswith(second['run_id']) for s in second['steps'])
            check('recipe replaces inputs and typed parameters with independent durable run and step records')
            failedrun=rt.run_recipe(recipe,{'sessions':dupe},run_dir=base/'run3')
            assert failedrun['status']=='checks_failed' and len(failedrun['steps'])==2
            bad=json.loads(json.dumps(recipe));bad['steps'][0]['sql']='SELECT missing FROM t'
            e=reject(lambda:rt.run_recipe(bad,{'sessions':before},run_dir=base/'run4'),'SQL_ERROR')
            assert e.run_record['status']=='failed' and e.run_record['steps'][0]['response']['job_id']
            bad['steps'][0]['bindings']={'t':'step:total'}
            reject(lambda:rt.run_recipe(bad,{'sessions':before},run_dir=base/'invalid'))
            assert not (base/'invalid').exists()
            leaked={'format':'rowtrail.recipe.v1','inputs':['source'],'steps':[
                {'id':'no_leakage','kind':'check','bindings':{'t':'input:source'},'forbidden_columns':['channel']}]}
            leakrun=rt.run_recipe(leaked,{'source':before},run_dir=base/'leakrun')
            assert leakrun['status']=='checks_failed'
            assert rt.lookup(second['steps'][0]['idempotency_key'])['job']['state']=='completed'
            reject(lambda:rt.lookup('unknown-key'),'OBJECT_NOT_FOUND')
            check('failed assertions stop later steps; SQL failure keeps accepted IDs and invalid DAGs fail before work')
            branch=rt.query('SELECT channel,SUM(amount) total FROM t GROUP BY channel ORDER BY channel',{'t':after},
                provenance={'description':'Channel revenue after normalization','origin':'beta2 integration'})
            package=rt.pack(branch,base/'package',notes='Reusable revenue analysis')
            assert len(package['nodes'])==3 and (base/'package/report.md').exists(),package
            assert package['nodes'][0]['preview']['rows']==rt.rows(branch)
            assert package['nodes'][0]['payload']['complete']
            verified=rt.verify_package(base/'package')
            assert verified['status']=='verified_bytes' and len(verified['missing_inputs'])==1
            assert not verified['sql_recomputed'] and not verified['original_sources_checked']
            check('branch package includes dependency SQL, parameters, identity, quality, notes, report and result payloads')
            with Client(str(bins/'rowtrail'),str(base/'receiver')) as receiver:
                receiver.call('doctor',{})
                imported=receiver.import_package(base/'package')
                actual=receiver.query('SELECT channel,total FROM t ORDER BY channel',{'t':imported})
                assert receiver.rows(actual)==rt.rows(branch)
                followup=receiver.query('SELECT SUM(total) FROM t',{'t':imported});assert receiver.scalar(followup)==75
                assert imported['verification']['missing_inputs']
                check('offline byte verification and fresh-workspace import support follow-ups without original files or SQL execution')
            full=rt.pack(branch,base/'full',include_inputs=True)
            assert rt.verify_package(base/'full')['missing_inputs']==[]
            with Client(str(bins/'rowtrail'),str(base/'receiver2')) as receiver:
                receiver.call('doctor',{})
                imported=receiver.import_package(base/'full')
                recipe=imported['recipe'];mapping={key:imported['mapping'][key] for key in recipe['inputs']}
                run=receiver.run_recipe(recipe,mapping,run_dir=base/'recompute')
                final=receiver.query('SELECT * FROM t',{'t':run['steps'][-1]['binding']})
                assert receiver.rows(final)==rt.rows(branch)
                check('included inputs yield an inert recipe that recomputes only on explicit run')
            payload=base/'package/n000.parquet';raw=payload.read_bytes();payload.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
            reject(lambda:rt.verify_package(base/'package'),'PACKAGE_CORRUPT');payload.write_bytes(raw)
            manifestpath=base/'package/manifest.json';original=manifestpath.read_text();malformed=json.loads(original)
            malformed['nodes'][0]['payload']['file']='../escape.parquet';manifestpath.write_text(json.dumps(malformed))
            reject(lambda:rt.verify_package(base/'package'));manifestpath.write_text(original)
            payload.unlink();payload.symlink_to(base/'full/n000.parquet')
            reject(lambda:rt.verify_package(base/'package'));payload.unlink();payload.write_bytes(raw)
            reject(lambda:rt.verify_package(base/'package',max_bytes=10),'PACKAGE_BUDGET_EXHAUSTED')
            reject(lambda:rt.pack(branch,base/'package'))
            check('tampering, traversal, symlink payloads, byte exhaustion and overwrite attempts are rejected')
            reject(lambda:rt.pack(branch,base/'small',max_nodes=1),'METADATA_LIMIT')
            assert (base/'small/failure.json').exists() and not (base/'small/manifest.json').exists()
            reject(lambda:rt.import_package(base/'small'))
            check('incomplete packages retain failure context and cannot be imported')
            forged=json.loads(original);forged['nodes'][0]['metadata']['row_count']='999'
            manifestpath.write_text(json.dumps(forged))
            e=reject(lambda:rt.import_package(base/'package'),'PACKAGE_CORRUPT')
            assert e.imported_bindings
            manifestpath.write_text(original)
            check('import checks recorded schema and row count against actual payloads and reports completed copies on failure')
            # Fresh import is detached from the delivered package too.
            with Client(str(bins/'rowtrail'),str(base/'receiver3')) as receiver:
                receiver.call('doctor',{})
                imported=receiver.import_package(base/'package')
                payload.unlink()
                assert receiver.scalar(receiver.query('SELECT SUM(total) FROM t',{'t':imported}))==75
            check('imported managed copies survive removal of delivered Parquet payloads')
    report={'status':'passed','checks':checks,'binary_sha256':{n:hashlib.sha256((bins/n).read_bytes()).hexdigest() for n in ('rowtrail','rowtrail-runtime')},'calls':len(traces)}
    path=ROOT/a.report;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n')
finally:
    for pid in pids:
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
