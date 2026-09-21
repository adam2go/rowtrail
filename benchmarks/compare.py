"""Deterministic execution comparison; this is NOT a real-Agent adoption trial.

Requires duckdb only in the benchmark environment, never in the product.
"""
import argparse, hashlib, json, os, pathlib, platform, signal, statistics, subprocess, tempfile, time
import duckdb

p=argparse.ArgumentParser()
p.add_argument('--bin-dir',default='target/release')
p.add_argument('--entry', choices=['cli','session'], default='cli')
p.add_argument('--repeats',type=int,default=3)
p.add_argument('--rows',type=int,default=16384)
p.add_argument('--output',default='benchmarks/local/latest.json')
args=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[1]
bins=(root/args.bin_dir).resolve()
queries=['SELECT region, SUM(amount), COUNT(*) FROM t GROUP BY region',
         'SELECT id, region, amount FROM t WHERE amount IS NOT NULL',
         'SELECT region, SUM(amount) FROM saved GROUP BY region',
         'SELECT COUNT(*) FROM saved WHERE amount > 0',
         'SELECT product, COUNT(*) FROM t GROUP BY product']
records=[]
with tempfile.TemporaryDirectory(prefix='rowtrail-benchmark-') as td:
    directory=pathlib.Path(td)
    subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',str(directory/'data'),'--rows',str(args.rows)],check=True)
    source=directory/'data/many.parquet'
    for repeat in range(args.repeats):
        workspace=directory/f'workspace-{repeat}'
        session=None
        started=time.perf_counter()
        if args.entry=='session':
            session=subprocess.Popen([str(bins/'rowtrail'),'--workspace',str(workspace),'session'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        def call(method,params):
            if session is not None:
                session.stdin.write(json.dumps({'api_version':'1','request_id':'benchmark','method':method,'params':params})+'\n');session.stdin.flush()
                value=json.loads(session.stdout.readline());assert value['ok'],value
                return value['result']
            result=subprocess.run([str(bins/'rowtrail'),'--workspace',str(workspace),'call',method],input=json.dumps(params),text=True,capture_output=True)
            assert result.returncode in (0,3), (result.returncode,result.stdout,result.stderr)
            value=json.loads(result.stdout);assert value['ok'],value
            return value['result']
        opened=call('open',{'source':str(source)})
        opened_ms=(time.perf_counter()-started)*1000
        binding={'t':{'dataset_ref':opened['dataset_ref'],'manifest_ref':opened['manifest_ref']}}
        stages=[];rowtrail_results=[]
        for index,sql in enumerate(queries):
            t=time.perf_counter()
            q=call('query',{'bindings':{'saved':saved} if index in (2,3) else binding,'sql':sql,'execution':{'wait_ms':1000,'preview':'none'}})
            while q['job']['state'] not in ('completed','failed','cancelled','interrupted','budget_exhausted'):
                q=call('control',{'action':'wait','ref':q['job']['id'],'wait_ms':1000})
            assert q['job']['state']=='completed',q
            ref={'result_ref':q['job']['result_ref'],'revision':q['readable_revision']}
            if index==1:saved=ref
            else:rowtrail_results.append(q['observation']['rows'] if q.get('observation') is not None else call('read',ref)['rows'])
            stages.append({'query':sql,'elapsed_ms':(time.perf_counter()-t)*1000,'metrics':q['job']['metrics']})
        rowtrail={'backend':'rowtrail','total_ms':(time.perf_counter()-started)*1000,'open_ms':opened_ms,'stages':stages,'entry':args.entry,'intermediate_storage':'durable Arrow parts'}
        doctor=call('doctor',{})
        if session is not None:
            session.stdin.close();session.wait(timeout=5)
        os.kill(doctor['coordinator_pid'],signal.SIGTERM)
        started=time.perf_counter();con=duckdb.connect();con.execute('SET threads=1')
        con.from_parquet(str(source)).create_view('t')
        stages=[];duck_results=[]
        for index,sql in enumerate(queries):
            t=time.perf_counter()
            if index==1:con.execute('CREATE TEMP TABLE saved AS '+sql)
            else:
                values=con.execute(sql).fetchall()
                duck_results.append([[None if v is None else str(v) for v in row] for row in values])
            stages.append({'query':sql,'elapsed_ms':(time.perf_counter()-t)*1000})
        duck={'backend':'duckdb','version':duckdb.__version__,'total_ms':(time.perf_counter()-started)*1000,'stages':stages,'intermediate_storage':'persistent in-process temp table'}
        for a,b in zip(rowtrail_results,duck_results):assert sorted(a,key=str)==sorted(b,key=str),(a,b)
        con.close()
        t=time.perf_counter()
        direct=json.loads(subprocess.check_output([str(bins/'rowtrail-runtime'),'benchmark','--source',str(source)]))
        direct['process_wall_ms']=(time.perf_counter()-t)*1000
        records.append({'repeat':repeat,'rowtrail':rowtrail,'duckdb':duck,'datafusion':direct})
    def scrub(value):
        if isinstance(value,str):return value.replace(str(directory),'<fixture>').replace(str(directory).lstrip('/'),'<fixture>')
        if isinstance(value,list):return [scrub(x) for x in value]
        if isinstance(value,dict):return {k:scrub(v) for k,v in value.items()}
        return value
    report={'kind':'deterministic execution comparison; no model calls','os':platform.platform(),'machine':platform.machine(),'binary_sha256':{name:hashlib.file_digest((bins/name).open('rb'),'sha256').hexdigest() for name in ['rowtrail','rowtrail-runtime']},'entry':args.entry,'fixture_rows':args.rows,'fixture_sha256':hashlib.file_digest(source.open('rb'),'sha256').hexdigest(),'fixture_bytes':source.stat().st_size,'cache':'OS cache not flushed; fresh RowTrail workspace and persistent engine sessions per repeat','records':scrub(records),'summary_ms':{name:statistics.median(r[name]['total_ms'] for r in records) for name in ['rowtrail','duckdb','datafusion']},'limitations':['This is not a same-Agent paired adoption experiment.','The baseline engines are allowed to retain intermediate tables in memory; RowTrail pays for disk durability and process isolation.','CLI startup is included for RowTrail; direct DataFusion process wall time is also recorded separately.','No claim of stable performance advantage is justified by these small local trials.']}
    path=root/args.output;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report['summary_ms'],indent=2))
