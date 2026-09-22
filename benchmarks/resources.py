"""Real coordinator/worker out-of-core sort with independent full-row verification.

DuckDB is only an independent Parquet reader here, not a product dependency.
"""
import argparse, hashlib, json, os, pathlib, platform, re, signal, sqlite3, subprocess, sys, tempfile, time
import duckdb
root=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'examples'))
from session_client import RowTrail
p=argparse.ArgumentParser()
p.add_argument('--rows',type=int,default=1048576)
p.add_argument('--bin-dir',default='target/release')
p.add_argument('--output',default='benchmarks/local/resources.json')
args=p.parse_args()
bins=(root/args.bin_dir).resolve()
with tempfile.TemporaryDirectory(prefix='rowtrail-resources-') as directory:
    base=pathlib.Path(directory);workspace=base/'workspace';data=base/'data'
    subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',str(data),'--rows',str(args.rows)],check=True)
    coordinator=None
    try:
        with RowTrail(str(bins/'rowtrail'),str(workspace)) as client:
            coordinator=client.call('doctor',{})['coordinator_pid']
            opened=client.call('open',{'source':str(data/'many.parquet')})
            started=time.perf_counter()
            job=client.call('query',{'bindings':{'t':{'dataset_ref':opened['dataset_ref'],'manifest_ref':opened['manifest_ref']}},'sql':'SELECT id,payload FROM t ORDER BY payload,id DESC','execution':{'wait_ms':0,'run_timeout_ms':60000,'memory_bytes':32*1024*1024,'spill_bytes':512*1024*1024,'scan_bytes':1024*1024*1024,'preview':'none'}})
            peak_rss=0
            while job['job']['state'] in ('queued','running','stopping'):
                job=client.call('control',{'action':'wait','ref':job['job']['id'],'wait_ms':25})
                with sqlite3.connect(workspace/'metadata.sqlite') as db:
                    pid=db.execute('SELECT worker_pid FROM jobs WHERE id=?',(job['job']['id'],)).fetchone()[0]
                if pid:
                    observed=subprocess.run(['ps','-o','rss=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
                    if observed:peak_rss=max(peak_rss,int(observed)*1024)
            assert job['job']['state']=='completed',job
            sort_ms=(time.perf_counter()-started)*1000
            with sqlite3.connect(workspace/'metadata.sqlite') as db:
                metrics=json.loads(db.execute('SELECT metrics FROM jobs WHERE id=?',(job['job']['id'],)).fetchone()[0])
                sizes=[r[0] for r in db.execute('SELECT bytes FROM parts WHERE result_id=?',(job['job']['result_ref'],))]
            assert metrics['rows']==args.rows,metrics
            assert max(sizes)<=8*1024*1024,sizes
            spill_count=sum(int(n) for n in re.findall(r'spill_count=(\d+)',metrics['plan']))
            assert spill_count>0,metrics['plan']
            target=base/'sorted.parquet'
            ref={'result_ref':job['job']['result_ref'],'revision':job['readable_revision']}
            started=time.perf_counter()
            exported=client.call('export',{**ref,'format':'parquet','destination':str(target),'execution':{'wait_ms':1000,'run_timeout_ms':60000}})
            while exported['job']['state'] in ('queued','running','stopping'):
                exported=client.call('control',{'action':'wait','ref':exported['job']['id'],'wait_ms':1000})
            assert exported['job']['state']=='completed',exported
            export_ms=(time.perf_counter()-started)*1000
            # The fixture payload begins with a fixed-width hex encoding of this
            # bijection. Its remaining suffix is constant, so this integer key
            # independently specifies every sorted row without engine SQL.
            expected=sorted(range(args.rows),key=lambda i:(i*6364136223846793005)&((1<<64)-1))
            connection=duckdb.connect()
            connection.execute('SET threads=1')
            cursor=connection.execute('SELECT id FROM read_parquet(?)',[str(target)])
            offset=0
            while values:=cursor.fetchmany(8192):
                actual=[value[0]-9007199254740993 for value in values]
                assert actual==expected[offset:offset+len(actual)],offset
                offset+=len(actual)
            assert offset==args.rows
            connection.close()
            def scrub(value):
                if isinstance(value,str):return value.replace(str(base),'<fixture>').replace(str(base).lstrip('/'),'<fixture>')
                if isinstance(value,dict):return {k:scrub(v) for k,v in value.items()}
                if isinstance(value,list):return [scrub(v) for v in value]
                return value
            report={'status':'passed','os':platform.platform(),'rows':args.rows,'fixture_sha256':hashlib.file_digest((data/'many.parquet').open('rb'),'sha256').hexdigest(),'source_bytes':(data/'many.parquet').stat().st_size,'engine_pool_bytes':32*1024*1024,'sampled_worker_peak_rss_bytes':peak_rss,'rss_note':'ps samples while waiting; not a hard bound or exact process peak','sort_wall_ms_including_observation':sort_ms,'export_wall_ms':export_ms,'spill_count':spill_count,'result_parts':len(sizes),'largest_part_bytes':max(sizes),'all_exported_ids_match_independent_sort':True,'sort_metrics':scrub(metrics),'export_metrics':scrub(exported['job']['metrics']),'binary_sha256':{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ['rowtrail','rowtrail-runtime']}}
            output=root/args.output;output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps({k:v for k,v in report.items() if k not in ('sort_metrics','export_metrics','binary_sha256')},indent=2))
    finally:
        if coordinator:
            try:os.kill(coordinator,signal.SIGTERM)
            except ProcessLookupError:pass
