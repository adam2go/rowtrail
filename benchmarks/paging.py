"""Bounded page reads of one sorted, immutable 16K-row result (no model calls)."""
import argparse, hashlib, json, os, pathlib, signal, statistics, subprocess, tempfile, time
root=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--bin-dir',default='target/release');p.add_argument('--output',default='benchmarks/local/paging.json');args=p.parse_args()
bins=(root/args.bin_dir).resolve()
with tempfile.TemporaryDirectory(prefix='rowtrail-paging-') as d:
    base=pathlib.Path(d);workspace=base/'w'
    subprocess.run([str(bins/'rowtrail-runtime'),'fixtures','--directory',d],check=True)
    def call(method,params):
        proc=subprocess.run([str(bins/'rowtrail'),'--workspace',str(workspace),'call',method],input=json.dumps(params),capture_output=True,text=True)
        value=json.loads(proc.stdout);assert value['ok'],value
        return value['result']
    pid=call('doctor',{})['coordinator_pid']
    try:
        opened=call('open',{'source':str(base/'many.parquet')})
        q=call('query',{'bindings':{'t':{'dataset_ref':opened['dataset_ref'],'manifest_ref':opened['manifest_ref']}},'sql':'SELECT id FROM t ORDER BY id','execution':{'wait_ms':1000,'preview':'none'}})
        while q['job']['state'] in ('queued','running'):
            q=call('control',{'action':'wait','ref':q['job']['id'],'wait_ms':1000})
        assert q['job']['state']=='completed',q
        ref={'result_ref':q['job']['result_ref'],'revision':q['readable_revision']}
        records=[]
        for rows in [100,1000,10000]:
            timings=[]
            for _ in range(5):
                started=time.perf_counter();page=call('read',{**ref,'max_rows':rows,'max_bytes':900000});timings.append((time.perf_counter()-started)*1000)
                assert page['rows']==[[str(9007199254740993+i)] for i in range(rows)]
            records.append({'rows':rows,'wall_ms_including_cli':timings,'median_ms':statistics.median(timings)})
        report={'kind':'warm-cache fixed result reads; same CLI entry for both versions','fixture_rows':16384,'records':records,'binary_sha256':{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ['rowtrail','rowtrail-runtime']}}
        path=root/args.output;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(records,indent=2))
    finally:os.kill(pid,signal.SIGTERM)
