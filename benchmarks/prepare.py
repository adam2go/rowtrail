"""Explicit CSV preparation break-even: conversion is included, never hidden.

DuckDB is a fixture writer only; all expected answers use Python integer cents.
"""
import argparse, collections, hashlib, json, os, pathlib, platform, signal, statistics, subprocess, tempfile, time
from decimal import Decimal
import duckdb

p=argparse.ArgumentParser();p.add_argument('--rows',type=int,default=1048576);p.add_argument('--repeats',type=int,default=5);p.add_argument('--bin-dir',default='target/release');p.add_argument('--output',default='benchmarks/local/prepare.json');args=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[1];bins=(root/args.bin_dir).resolve()
queries=['SELECT COUNT(*),SUM(amount) FROM t','SELECT region,COUNT(*),SUM(amount) FROM t GROUP BY region','SELECT product,COUNT(*) FROM t GROUP BY product','SELECT MIN(id),MAX(id),COUNT(amount) FROM t','SELECT COUNT(*) FROM t WHERE amount>0']*2
schema=[{'name':'id','type':'Int64'},{'name':'region','type':'Utf8'},{'name':'product','type':'Utf8'},{'name':'amount','type':'Decimal128(20, 2)'}]
groups=collections.defaultdict(lambda:[0,0]);products=collections.Counter();total=nonnull=positive=0
for i in range(args.rows):
    region=None if i%13==0 else ['华东','华南','华北'][i%3];groups[region][0]+=1;products['A' if i%2==0 else 'B']+=1
    if i%17:
        cents=i%2001-1000;groups[region][1]+=cents;total+=cents;nonnull+=1;positive+=cents>0
money=lambda cents:format(Decimal(cents)/100,'.2f')
expected=[[[str(args.rows),money(total)]],[[r,str(n),money(c)] for r,(n,c) in groups.items()],[[k,str(v)] for k,v in products.items()],[[str(9007199254740993),str(9007199254740993+args.rows-1),str(nonnull)]],[[str(positive)]]]*2
records=[]
with tempfile.TemporaryDirectory(prefix='rowtrail-prepare-bench-') as td:
    base=pathlib.Path(td);subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',str(base/'data'),'--rows',str(args.rows)],check=True,stdout=subprocess.DEVNULL)
    source=base/'source.csv';con=duckdb.connect();con.execute('COPY (SELECT id,region,product,amount FROM read_parquet($source)) TO $destination (HEADER, FORMAT CSV)',{'source':str(base/'data/many.parquet'),'destination':str(source)});con.close()
    for repeat in range(args.repeats):
        for mode in (['csv','prepared'] if repeat%2==0 else ['prepared','csv']):
            workspace=base/f'{repeat}-{mode}';started=time.perf_counter()
            session=subprocess.Popen([str(bins/'rowtrail'),'--workspace',str(workspace),'session'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            def call(method,params):
                session.stdin.write(json.dumps({'api_version':'1','request_id':'bench','method':method,'params':params})+'\n');session.stdin.flush()
                result=json.loads(session.stdout.readline());assert result['ok'],result;return result['result']
            def wait(v):
                while v['job']['state'] in ('queued','running','stopping'):v=call('control',{'action':'wait','ref':v['job']['id'],'wait_ms':1000})
                assert v['job']['state']=='completed',v;return v
            def rows(v):
                return v['observation']['rows'] if v.get('observation') else call('read',{'result_ref':v['job']['result_ref'],'revision':v['readable_revision']})['rows']
            opened=call('open',{'source':str(source),'schema':schema});binding={k:opened[k] for k in ('dataset_ref','manifest_ref')};open_ms=(time.perf_counter()-started)*1000;prepare_ms=0;prepare_metrics=None;prepared_bytes=None
            if mode=='prepared':
                t=time.perf_counter();prepared=call('prepare',{'source':binding,'execution':{'wait_ms':1000}});done=wait(prepared);prepare_ms=(time.perf_counter()-t)*1000;prepare_metrics=done['job']['metrics'];binding={k:prepared[k] for k in ('dataset_ref','manifest_ref')};prepared_bytes=call('workspace',{'action':'usage'})['stored_bytes']
            t=time.perf_counter();profile=wait(call('inspect',{'ref':binding['manifest_ref'],'columns':['amount'],'checks':['null_count','min_max'],'execution':{'wait_ms':1000}}));profile_ms=(time.perf_counter()-t)*1000
            assert rows(profile)==[[str(args.rows),str(args.rows-nonnull),'-10.00','10.00']]
            stages=[]
            for index,sql in enumerate(queries):
                t=time.perf_counter();done=wait(call('query',{'bindings':{'t':binding},'sql':sql,'execution':{'wait_ms':1000,'preview':'none'}}));actual=rows(done)
                assert sorted(actual,key=str)==sorted(expected[index],key=str),(actual,expected[index])
                stages.append({'elapsed_ms':(time.perf_counter()-t)*1000,'cumulative_ms':(time.perf_counter()-started)*1000,'metrics':done['job']['metrics']})
            total_ms=(time.perf_counter()-started)*1000;pid=call('doctor',{})['coordinator_pid'];session.stdin.close();session.wait(timeout=5);os.kill(pid,signal.SIGTERM)
            records.append({'repeat':repeat,'mode':mode,'open_ms':open_ms,'prepare_ms':prepare_ms,'prepare_metrics':prepare_metrics,'profile_ms':profile_ms,'profile_metrics':profile['job']['metrics'],'prepared_bytes':prepared_bytes,'total_ms':total_ms,'stages':stages})
    def scrub(v):
        if isinstance(v,str):return v.replace(str(base),'<fixture>').replace(str(base).lstrip('/'),'<fixture>')
        if isinstance(v,list):return [scrub(x) for x in v]
        if isinstance(v,dict):return {k:scrub(x) for k,x in v.items()}
        return v
    summary={mode:{name:statistics.median(r[name] for r in records if r['mode']==mode) for name in ['open_ms','prepare_ms','profile_ms','total_ms']} for mode in ['csv','prepared']}
    cumulative={mode:[statistics.median(r['stages'][i]['cumulative_ms'] for r in records if r['mode']==mode) for i in range(len(queries))] for mode in ['csv','prepared']}
    break_even=next((i+1 for i,(a,b) in enumerate(zip(cumulative['csv'],cumulative['prepared'])) if b<a),None)
    report={'kind':'CSV versus explicit preparation in RowTrail, including conversion','rows':args.rows,'source_bytes':source.stat().st_size,'repeats':args.repeats,'os':platform.platform(),'machine':platform.machine(),'binary_sha256':{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ['rowtrail','rowtrail-runtime']},'queries':queries,'summary_ms':summary,'median_cumulative_ms':cumulative,'first_cheaper_followup_count':break_even,'records':scrub(records),'conditions':['Warm OS cache; fresh workspace and native session each run; mode order alternates.','Timing includes session start, open, optional prepare, one profile, ten exact aggregate questions and observations.','All results match independent integer-cent Python oracle.','DuckDB writes the fixture only; no direct-engine performance comparison here.','Hash-verification I/O is charged; immutable prepared reads count as result bytes.','Results depend on width, types, selectivity, disk and number of follow-ups; no automatic preparation policy.']}
    path=root/args.output;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'summary':summary,'break_even_followups':break_even},indent=2))
