"""Beta.2 complete analysis handoff; independent Python integer/Decimal oracle.

No model calls, dataframe dependencies or full-table Python fetch. Measures the
new workflow, not a performance comparison with unsupported beta.1 features.
"""
import argparse,hashlib,json,os,pathlib,platform,signal,statistics,subprocess,sys,tempfile,time
from decimal import Decimal
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bin-dir',default='target/release')
p.add_argument('--rows',type=int,default=131072);p.add_argument('--repeats',type=int,default=5)
p.add_argument('--output',default='benchmarks/local/analysis-workflow.json');a=p.parse_args()
bins=(ROOT/a.bin_dir).resolve();records=[];pids=set()
kept=[i for i in range(a.rows) if (9007199254740993+i)%13]
expected_count=len(kept);expected_total=sum(i%2001-1000+1 for i in kept if i%17)
expected_changed_amount=sum(i%17!=0 for i in kept)
del kept
try:
    with tempfile.TemporaryDirectory(prefix='rowtrail-analysis-bench-') as td:
        base=pathlib.Path(td)
        subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',str(base/'data'),'--rows',str(a.rows)],check=True,stdout=subprocess.DEVNULL)
        source=base/'data/many.parquet'
        for repeat in range(a.repeats):
            calls=[];times={};directory=base/str(repeat);directory.mkdir()
            class Metered(RowTrail):
                def call(self,method,params,idempotency_key=None):
                    start=time.perf_counter();v=super().call(method,params,idempotency_key)
                    calls.append({'method':method,'ms':(time.perf_counter()-start)*1000,'response_bytes':self.last_response_bytes})
                    if method=='doctor':pids.add(v['coordinator_pid'])
                    return v
            def measure(name,fn):
                start=time.perf_counter();v=fn();times[name]=(time.perf_counter()-start)*1000;return v
            begin=time.perf_counter()
            with Metered(str(bins/'rowtrail'),str(directory/'workspace')) as rt:
                rt.call('doctor',{})
                snap=measure('snapshot_ms',lambda:rt.from_parquet(source,provenance={'description':'Benchmark cleaned input'}))
                before=measure('before_ms',lambda:rt.query('SELECT id,region,amount FROM t',{'t':snap},execution={'output':{'max_rows':0}}))
                after=measure('after_ms',lambda:rt.query("SELECT id,COALESCE(region,'unknown') || '_v2' region,CAST(amount+0.01::DECIMAL(20,2) AS DECIMAL(20,2)) amount FROM t WHERE id % 13 <> 0",{'t':snap},execution={'output':{'max_rows':0}}))
                diff=measure('diff_ms',lambda:rt.diff(before,after,keys=['id'],samples=5))
                assert diff['rows']=={'before':a.rows,'after':expected_count,'added':0,'deleted':a.rows-expected_count,'modified':expected_count},diff['rows']
                assert diff['changed_fields']=={'region':expected_count,'amount':expected_changed_amount},diff['changed_fields']
                assertion=measure('check_ms',lambda:rt.check('SELECT id FROM t GROUP BY id HAVING COUNT(*)>1',{'t':after},samples=5))
                assert assertion['passed']
                result=measure('summary_ms',lambda:rt.query('SELECT COUNT(*) n,SUM(amount) total FROM t',{'t':after}))
                assert rt.rows(result)==[[str(expected_count),format(Decimal(expected_total)/100,'.2f')]]
                package=measure('pack_ms',lambda:rt.pack(result,directory/'package',include_inputs=True))
                measure('offline_verify_ms',lambda:rt.verify_package(directory/'package'))
            with Metered(str(bins/'rowtrail'),str(directory/'recipient')) as rt:
                rt.call('doctor',{})
                imported=measure('import_ms',lambda:rt.import_package(directory/'package'))
                verified=measure('followup_ms',lambda:rt.query('SELECT * FROM t',{'t':imported}))
                assert rt.rows(verified)==[[str(expected_count),format(Decimal(expected_total)/100,'.2f')]]
                recipe=imported['recipe'];inputs={n:imported['mapping'][n] for n in recipe['inputs']}
                run=measure('rerun_ms',lambda:rt.run_recipe(recipe,inputs,run_dir=directory/'run'))
                final=rt.query('SELECT * FROM t',{'t':run['steps'][-1]['binding']})
                assert rt.rows(final)==rt.rows(verified)
            total=(time.perf_counter()-begin)*1000
            records.append({'repeat':repeat,'total_ms':total,**times,'calls':calls,
                'call_count':len(calls),'response_bytes':sum(c['response_bytes'] for c in calls),
                'package_bytes':sum(f.stat().st_size for f in (directory/'package').iterdir()),
                'package_nodes':len(package['nodes']),'independent_oracle_passed':True})
            print(repeat,round(total,2),flush=True)
            for pid in pids:
                try:os.kill(pid,signal.SIGTERM)
                except ProcessLookupError:pass
            pids.clear()
        report={'status':'passed','kind':__doc__,'platform':platform.platform(),'rows':a.rows,'repeats':a.repeats,
            'binary_sha256':{n:hashlib.sha256((bins/n).read_bytes()).hexdigest() for n in ('rowtrail','rowtrail-runtime')},
            'fixture_sha256':hashlib.file_digest(source.open('rb'),'sha256').hexdigest(),
            'oracle':{'after_rows':expected_count,'after_sum_cents':expected_total,'changed_amounts':expected_changed_amount},
            'records':records,'summary':{k:statistics.median(r[k] for r in records) for k in ['total_ms',*times,'call_count','response_bytes','package_bytes']},
            'limitations':['Single machine; serial trials; OS cache not flushed; no external model or adoption evidence.',
                'Diff performs repeated full native scans; samples bound output, not scan cost.',
                'Package import includes checksum verification, a private streamed copy, native snapshot and schema/count validation.',
                'Workspaces, complete failures/results and output packages pay for durability; no task-speed comparison with bare SQL is claimed.']}
        out=ROOT/a.output;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report['summary'],indent=2))
finally:
    for pid in pids:
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
