"""End-to-end scalar retrieval under 2 KiB and 8 KiB budgets, alternating versions."""
import argparse,hashlib,json,os,pathlib,platform,signal,statistics,sys,tempfile,time
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail
p=argparse.ArgumentParser();p.add_argument('--variant',action='append',required=True)
p.add_argument('--repeats',type=int,default=7);p.add_argument('--queries',type=int,default=100)
p.add_argument('--output',default='benchmarks/local/answer-budget.json');a=p.parse_args()
variants={n:(ROOT/d).resolve() for n,d in (v.split('=',1) for v in a.variant)};records=[]
with tempfile.TemporaryDirectory(prefix='rowtrail-answer-') as td:
    for repeat in range(a.repeats):
        order=list(variants)
        if repeat%2:order.reverse()
        for name in order:
            for budget in (2048,8192):
                calls=[];pid=None
                class Metered(RowTrail):
                    def call(self,method,params,idempotency_key=None):
                        v=super().call(method,params,idempotency_key)
                        calls.append({'method':method,'bytes':self.last_response_bytes});return v
                try:
                    with Metered(str(variants[name]/'rowtrail'),str(pathlib.Path(td)/f'{repeat}-{name}-{budget}')) as rt:
                        pid=rt.call('doctor',{})['coordinator_pid']
                        rt.query('SELECT 0 answer') # start the worker outside warm timing
                        for i in range(a.queries):
                            start_calls=len(calls);start=time.perf_counter()
                            result=rt.query(f'SELECT {i} answer',execution={'output':{'max_rows':1,'max_bytes':budget}})
                            elapsed=(time.perf_counter()-start)*1000
                            assert rt.scalar(result)==i
                            used=calls[start_calls:]
                            assert all(c['bytes']<=budget for c in used)
                            records.append({'variant':name,'repeat':repeat,'index':i,'budget':budget,
                                'ms':elapsed,'calls':len(used),'bytes':sum(c['bytes'] for c in used)})
                finally:
                    if pid:
                        try:os.kill(pid,signal.SIGTERM)
                        except ProcessLookupError:pass
summary={n:{str(b):{k:statistics.median(r[k] for r in records if r['variant']==n and r['budget']==b)
    for k in ('ms','calls','bytes')} for b in (2048,8192)} for n in variants}
report={'status':'passed','kind':__doc__,'platform':platform.platform(),'repeats':a.repeats,'queries_per_budget_per_repeat':a.queries,
    'binary_sha256':{n:{f:hashlib.sha256((d/f).read_bytes()).hexdigest() for f in ('rowtrail','rowtrail-runtime')} for n,d in variants.items()},
    'records':records,'summary':summary,'limitations':['Single machine, warmed workers, fresh workspace per budget/repeat, alternating versions.',
        'No external model; bytes are UTF-8 response envelopes, not tokenizer estimates. Scalar includes complete quality; small budget omits optional job metrics.']}
out=ROOT/a.output;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(summary,indent=2))
