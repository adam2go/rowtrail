"""Progressive file checkpoints: independent arithmetic, lifecycle and reuse."""
import argparse, hashlib, json, os, pathlib, signal, sqlite3, subprocess, tempfile, time
from decimal import Decimal, ROUND_DOWN

p=argparse.ArgumentParser();p.add_argument('--bin-dir',default='target/release');p.add_argument('--report',default='benchmarks/local/alpha4.json');args=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[2];bins=(root/args.bin_dir).resolve();traces=[];checks=[];terminal={'completed','failed','cancelled','interrupted','budget_exhausted'}
with tempfile.TemporaryDirectory(prefix='rowtrail-alpha4-') as td:
    base=pathlib.Path(td);ws=base/'workspace'
    client=subprocess.Popen([str(bins/'rowtrail'),'--workspace',str(ws),'session'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    def call(method,params,ok=True):
        req={'api_version':'1','request_id':str(len(traces)),'method':method,'params':params};client.stdin.write(json.dumps(req)+'\n');client.stdin.flush();r=json.loads(client.stdout.readline());traces.append({'request':req,'response':r})
        if ok:assert r['ok'],r
        return r['result'] if r['ok'] else r
    def wait(r,success=True):
        stop=time.monotonic()+60
        while r['job']['state'] not in terminal:
            assert time.monotonic()<stop,r
            r=call('control',{'action':'wait','ref':r['job']['id'],'wait_ms':1000})
        if success:assert r['job']['state']=='completed',r
        return r
    def binding(r):return {k:r[k] for k in ('dataset_ref','manifest_ref')}
    def ref(r,rev=None):return {'result_ref':r['job']['result_ref'],'revision':rev or r['readable_revision']}
    def query(bindings,sql):return wait(call('query',{'bindings':bindings,'sql':sql,'execution':{'wait_ms':1000}}))
    def read(r,rev=None):return call('read',ref(r,rev))
    def check(s):checks.append(s);print('PASS',s,flush=True)
    pid=None;succeeded=False
    try:
        pid=call('doctor',{})['coordinator_pid']
        fragments=base/'fragments';fragments.mkdir();all_values=[];prefix=[]
        schema=[{'name':'id','type':'Int64'},{'name':'amount','type':'Decimal128(20, 2)'},{'name':'u','type':'UInt64'},{'name':'label','type':'Utf8'}]
        for f in range(12):
            values=[(9007199254740993+f*100+i,None if i%3==0 else (f*7-i*11),18446744073709550000+i,None if i%2 else 'x') for i in range(f+1)]
            path=base/f'{f}.csv';path.write_text('id,amount,u,label\n'+''.join(f'{i},{"" if c is None else format(Decimal(c)/100,".2f")},{u},{s or ""}\n' for i,c,u,s in values))
            source=call('open',{'source':str(path),'schema':schema});rows=query({'t':binding(source)},'SELECT * FROM t')
            wait(call('export',{**ref(rows),'format':'parquet','destination':str(fragments/f'{f:02}.parquet')}))
            all_values.extend(values);prefix.append(list(all_values))
        opened=call('open',{'source':str(fragments),'format':'parquet'})
        aggregates=[{'function':'count','alias':'rows'},{'function':'count','column':'amount','alias':'nonnull'},{'function':'sum','column':'amount','alias':'total'},{'function':'avg','column':'amount','alias':'mean'},{'function':'sum','column':'id','alias':'large_sum'},{'function':'sum','column':'u','alias':'unsigned_sum'},{'function':'count','column':'label','alias':'labels'}]
        def expected(values):
            cents=[x[1] for x in values if x[1] is not None];total=sum(cents)
            average=None if not cents else format((Decimal(total)/100/len(cents)).quantize(Decimal('.000001'),rounding=ROUND_DOWN),'.6f')
            return [[str(len(values)),str(len(cents)),None if not cents else format(Decimal(total)/100,'.2f'),average,str(sum(x[0] for x in values)),str(sum(x[2] for x in values)),str(sum(x[3] is not None for x in values))]]
        done=wait(call('analyze',{'source':binding(opened),'aggregates':aggregates,'execution':{'wait_ms':1000}}))
        assert done['readable_revision']==13,done
        for n,values in enumerate(prefix,1):
            snapshot=read(done,n)
            assert snapshot['rows']==expected(values),(n,snapshot,expected(values))
            assert snapshot['quality']['coverage']['kind']=='partial' and not snapshot['quality']['final_for_request']
            assert snapshot['quality']['coverage']['input_coverage']=={'unit':'manifest_file','completed_files':n,'total_files':12,'order':'frozen_manifest_order'}
            assert len(snapshot['rows'])==1 and not snapshot['next_cursor']
        final=read(done);assert final['rows']==expected(all_values)
        assert final['quality']['coverage']['kind']=='complete' and final['quality']['final_for_request']
        check('every immutable checkpoint matches a Python integer/Decimal prefix oracle; final snapshot contains one row')
        branch=query({'p':ref(done,3)},'SELECT rows,total FROM p')
        observed=read(branch);assert observed['quality']['coverage']['kind']=='partial'
        assert branch['job']['metrics']['io']['source_read_bytes']==0
        assert observed['rows']==[[str(len(prefix[2])),expected(prefix[2])[0][2]]]
        destination=base/'partial.parquet'
        rejected=call('export',{**ref(done,3),'format':'parquet','destination':str(destination)},ok=False)
        assert rejected['error']['code']=='INVALID_ARGUMENT',rejected
        wait(call('export',{**ref(done,3),'format':'parquet','destination':str(destination),'allow_nonfinal':True}))
        again=call('open',{'source':str(destination)})
        imported=query({'p':binding(again)},'SELECT rows,total FROM p')
        assert read(imported)['rows']==observed['rows'] and read(imported)['quality']['coverage']['kind']=='partial'
        check('a fixed partial checkpoint can be queried/exported/reopened with partial quality preserved')
        silent=wait(call('analyze',{'source':binding(opened),'aggregates':aggregates,'execution':{'preview':'none','wait_ms':1000}}))
        assert silent['readable_revision']==1 and read(silent)['rows']==final['rows']
        check('preview none writes exactly one final checkpoint')
        events=call('events',{'job_ids':[done['job']['id']],'types':['result.ready'],'max_bytes':65536})
        assert [e['payload']['revision'] for e in events['events']]==list(range(1,14))
        check('durable result-ready events describe each committed aggregate revision')
        db=sqlite3.connect(ws/'metadata.sqlite')
        first_bytes=db.execute('SELECT bytes FROM parts WHERE result_id=? AND seq=0',(ref(done)['result_ref'],)).fetchone()[0]
        exhausted=wait(call('analyze',{'source':binding(opened),'aggregates':aggregates,'execution':{'result_bytes':first_bytes+32,'wait_ms':1000}}),False)
        assert exhausted['job']['state']=='budget_exhausted' and exhausted['readable_revision']==1,exhausted
        assert read(exhausted)['rows']==expected(prefix[0]) and read(exhausted)['quality']['coverage']['kind']=='partial'
        check('result budget exhaustion keeps the last complete file checkpoint, not half-updated aggregates')
        for bad in [[],[{'function':'median','column':'amount','alias':'x'}],[{'function':'sum','alias':'x'}],[{'function':'avg','column':'label','alias':'x'}],[{'function':'count','alias':'x'},{'function':'count','alias':'x'}]]:
            assert not call('analyze',{'source':binding(opened),'aggregates':bad},ok=False)['ok']
        denied=call('analyze',{'source':binding(source),'aggregates':aggregates},ok=False)
        assert denied['error']['code']=='UNSUPPORTED_OPERATION'
        check('unsupported functions, types, aliases and CSV sources fail before accepting a job')
        empty=query({},'SELECT CAST(NULL AS BIGINT) n WHERE false');wait(call('export',{**ref(empty),'format':'parquet','destination':str(base/'empty.parquet')}));e=call('open',{'source':str(base/'empty.parquet')})
        empty_agg=wait(call('analyze',{'source':binding(e),'aggregates':[{'function':'count','alias':'n'},{'function':'sum','column':'n','alias':'s'},{'function':'avg','column':'n','alias':'a'}],'execution':{'wait_ms':1000}}))
        assert read(empty_agg)['rows']==[['0',None,None]]
        check('empty input retains typed count zero and null sum/average')
        # Cancellation test uses many nonempty fragments, so at least one exact
        # prefix is durable before external cancellation reaches the worker.
        bigdir=base/'many-fragments';bigdir.mkdir()
        import shutil
        for i in range(128):shutil.copyfile(fragments/'11.parquet',bigdir/f'{i:03}.parquet')
        large=call('open',{'source':str(bigdir),'format':'parquet'})
        active=call('analyze',{'source':binding(large),'aggregates':aggregates,'execution':{'wait_ms':0}})
        deadline=time.monotonic()+10
        while not active['job']['readable_revision']:
            assert time.monotonic()<deadline and active['job']['state'] not in terminal,active
            active=call('control',{'action':'status','ref':active['job']['id']})
        fixed_revision=active['job']['readable_revision'];before=read(active,fixed_revision)
        call('control',{'action':'cancel','ref':active['job']['id']});stopped=wait(active,False)
        assert stopped['job']['state']=='cancelled' and stopped['job']['metrics']['worker_exit_confirmed'],stopped
        assert read(stopped,fixed_revision)['rows']==before['rows'] and read(stopped)['quality']['coverage']['kind']=='partial'
        check('true cancellation preserves fixed earlier aggregate checkpoints')
        active=call('analyze',{'source':binding(large),'aggregates':aggregates,'execution':{'wait_ms':0}})
        deadline=time.monotonic()+10
        while not active['job']['readable_revision']:
            assert time.monotonic()<deadline and active['job']['state'] not in terminal,active
            active=call('control',{'action':'status','ref':active['job']['id']})
        worker_pid=db.execute('SELECT worker_pid FROM jobs WHERE id=?',(active['job']['id'],)).fetchone()[0]
        os.kill(worker_pid,signal.SIGKILL)
        interrupted=wait(active,False)
        assert interrupted['job']['state']=='interrupted' and len(read(interrupted)['rows'])==1,interrupted
        assert read(interrupted)['quality']['coverage']['kind']=='partial'
        check('worker crash preserves one complete aggregate checkpoint and reports interrupted')
        overflow_dir=base/'overflow';overflow_dir.mkdir()
        huge=query({},"SELECT CAST('99999999999999999999999999999999999999' AS DECIMAL(38,0)) n")
        wait(call('export',{**ref(huge),'format':'parquet','destination':str(overflow_dir/'a.parquet')}))
        shutil.copyfile(overflow_dir/'a.parquet',overflow_dir/'b.parquet')
        overflow_source=call('open',{'source':str(overflow_dir),'format':'parquet'})
        overflow=wait(call('analyze',{'source':binding(overflow_source),'aggregates':[{'function':'sum','column':'n','alias':'s'}],'execution':{'wait_ms':1000}}),False)
        assert overflow['job']['error']['code']=='ARITHMETIC_OVERFLOW' and overflow['readable_revision']==1,overflow
        assert read(overflow)['rows']==[['99999999999999999999999999999999999999']]
        check('numeric overflow is explicit and never replaces a valid checkpoint with a wrapped sum')

        succeeded=True
    finally:
        client.stdin.close();client.wait(timeout=5)
        if pid:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        report={'status':'passed' if succeeded else 'incomplete','checks':checks,'binary_sha256':{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ['rowtrail','rowtrail-runtime']},'traces':traces}
        path=root/args.report;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':report['status'],'checks':len(checks)}))
