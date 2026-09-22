"""Save a large subset, ten bounded follow-ups, reconnect and discover by label.

Python integer arithmetic is the independent oracle. No model calls, full-table
observations, hidden query retries or cache flush. Serial alternating trials.
"""
import argparse, hashlib, json, os, pathlib, platform, signal, sqlite3, statistics
import subprocess, sys, tempfile, time
from decimal import Decimal
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--variant',action='append',required=True);p.add_argument('--rows',type=int,default=2097152)
p.add_argument('--repeats',type=int,default=7);p.add_argument('--output',default='benchmarks/local/followup.json')
a=p.parse_args();variants={name:(ROOT/path).resolve() for name,path in (v.split('=',1) for v in a.variant)}
oracle=[];queries=[]
for mod in range(2,12):
    count=ids=amount=amount_count=0;first=last=None
    for i in range(a.rows):
        identity=9007199254740993+i
        if identity%5==0 or identity%mod!=1:continue
        count+=1;ids+=identity
        first=identity if first is None else first;last=identity
        if i%17:amount+=i%2001-1000;amount_count+=1
    oracle.append([[str(count),format(Decimal(amount)/100,'.2f') if amount_count else None,
                    str(ids) if count else None,str(first) if first is not None else None,str(last) if last is not None else None]])
    queries.append(f'SELECT COUNT(*),SUM(amount),SUM(CAST(id AS DECIMAL(38,0))),MIN(id),MAX(id) FROM saved WHERE id % {mod} = 1')
records=[]
with tempfile.TemporaryDirectory(prefix='rowtrail-followup-') as td:
    base=pathlib.Path(td);source=base/'data/many.parquet'
    subprocess.run([str(next(iter(variants.values()))/'rowtrail-runtime'),'fixtures','--directory',str(base/'data'),'--rows',str(a.rows)],check=True,stdout=subprocess.DEVNULL)
    for repeat in range(a.repeats):
        names=list(variants)
        if repeat%2:names.reverse()
        for name in names:
            bins=variants[name];ws=base/f'{repeat}-{name}';pid=None;stages=[];calls=[]
            class Metered(RowTrail):
                def call(self,method,params,idempotency_key=None):
                    started=time.perf_counter();value=super().call(method,params,idempotency_key)
                    calls.append({'method':method,'wall_ms':(time.perf_counter()-started)*1000,'wire_bytes':self.last_response_bytes})
                    return value
            begin=time.perf_counter()
            try:
                with Metered(str(bins/'rowtrail'),str(ws)) as rt:
                    start=time.perf_counter();opened=rt.open(source)
                    open_ms=(time.perf_counter()-start)*1000
                    start=time.perf_counter();saved=rt.query('SELECT id,region,amount,payload FROM t WHERE id % 5 <> 0',{'t':opened},label='followup subset',execution={'output':{'max_rows':0}})
                    save_ms=(time.perf_counter()-start)*1000;fixed=rt.binding(saved)
                    for index,sql in enumerate(queries):
                        start=time.perf_counter();value=rt.query(sql,{'saved':fixed})
                        elapsed=(time.perf_counter()-start)*1000
                        assert rt.rows(value)==oracle[index],(name,index,rt.rows(value),oracle[index])
                        assert value['job']['metrics']['io']['source_read_bytes']==0
                        stages.append({'index':index,'wall_ms':elapsed,'metrics':value['job']['metrics']})
                reconnect_start=time.perf_counter()
                with Metered(str(bins/'rowtrail'),str(ws)) as rt:
                    item=rt.find('followup subset')
                    assert rt.binding(item)==fixed
                    discover_ms=(time.perf_counter()-reconnect_start)*1000
                    start=time.perf_counter();again=rt.query(queries[0],{'saved':item})
                    resumed_ms=(time.perf_counter()-start)*1000
                    assert rt.rows(again)==oracle[0]
                    assert again['job']['metrics']['io']['source_read_bytes']==0
                    total_ms=(time.perf_counter()-begin)*1000
                    # Metadata verification after timing does not enter the workload.
                    pid=rt.call('doctor',{})['coordinator_pid']
                with sqlite3.connect(ws/'metadata.sqlite') as db:
                    parts=[r[0] for r in db.execute('SELECT bytes FROM parts WHERE result_id=?',[fixed['result_ref']])]
                assert parts and max(parts)<=8388608
                records.append({'variant':name,'repeat':repeat,'total_ms':total_ms,'open_ms':open_ms,'save_ms':save_ms,
                    'ten_queries_ms':sum(s['wall_ms'] for s in stages),'reconnect_discover_ms':discover_ms,'resumed_query_ms':resumed_ms,
                    'save_metrics':saved['job']['metrics'],'stages':stages,'resumed_metrics':again['job']['metrics'],
                    'calls':calls[:-1],'total_response_bytes':sum(c['wire_bytes'] for c in calls[:-1]),
                    'saved_parts':len(parts),'saved_bytes':sum(parts),'independent_oracle_passed':True})
                print(name,repeat,round(total_ms,2),flush=True)
            finally:
                if pid:
                    try:os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError:pass
    summary={name:{key:statistics.median(r[key] for r in records if r['variant']==name) for key in
        ('total_ms','save_ms','ten_queries_ms','reconnect_discover_ms','resumed_query_ms','total_response_bytes')} for name in variants}
    report={'status':'passed','kind':__doc__,'rows':a.rows,'repeats':a.repeats,'platform':platform.platform(),
        'fixture_bytes':source.stat().st_size,'fixture_sha256':hashlib.file_digest(source.open('rb'),'sha256').hexdigest(),
        'binary_sha256':{name:{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ('rowtrail','rowtrail-runtime')} for name,bins in variants.items()},
        'sql':queries,'oracle':oracle,'records':records,'summary':summary,
        'limitations':['Single machine; OS cache not flushed, fresh workspace per trial; no external model or adoption claim.',
                       'Planning/write/commit/verification counters are components, not an additive decomposition of parallel wall time.',
                       'Each new job revalidates saved bytes; this is not a cross-job content cache.',
                       'Response bytes include envelopes and diagnostics; no tokenizer-based token estimate is claimed.']}
    out=ROOT/a.output;out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2).replace(str(base),'<fixture>').replace(str(base).lstrip('/'),'<fixture>')+'\n')
    print(json.dumps(summary,indent=2))
