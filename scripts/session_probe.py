"""Verify persistent NDJSON composition, malformed frames and durable disconnects."""
import json, os, pathlib, signal, subprocess, sys, tempfile

binary=str(pathlib.Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix='rowtrail-session-') as directory:
    workspace=pathlib.Path(directory)/'workspace'
    process=subprocess.Popen([binary,'--workspace',str(workspace),'session'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    coordinator=None
    def raw(value):
        process.stdin.write(value+'\n');process.stdin.flush()
        return json.loads(process.stdout.readline())
    def call(method,params,identifier='session'):
        reply=raw(json.dumps({'api_version':'1','request_id':identifier,'method':method,'params':params}))
        assert reply['ok'] and reply['request_id']==identifier,reply
        return reply['result']
    try:
        coordinator=call('doctor',{})['coordinator_pid']
        assert raw('{')['error']['code']=='INVALID_ARGUMENT'
        query=call('query',{'bindings':{},'sql':'SELECT CAST(9007199254740993 AS BIGINT) id','execution':{'wait_ms':1000}})
        while query['job']['state'] in ('queued','running'):
            query=call('control',{'action':'wait','ref':query['job']['id'],'wait_ms':1000})
        ref={'result_ref':query['job']['result_ref'],'revision':query['readable_revision']}
        assert call('read',ref)['rows']==[['9007199254740993']]
        queued=call('query',{'bindings':{},'sql':'SELECT 42 value','execution':{'wait_ms':0}})
        process.stdin.close();process.wait(timeout=5)
        # Closing the agent connection does not cancel its accepted background work.
        done=json.loads(subprocess.run([binary,'--workspace',str(workspace),'call','control'],input=json.dumps({'action':'wait','ref':queued['job']['id'],'wait_ms':1000}),capture_output=True,text=True).stdout)['result']
        assert done['job']['state']=='completed',done
        assert json.loads(subprocess.check_output([binary,'--workspace',str(workspace),'read',ref['result_ref'],'--revision',str(ref['revision'])]))['result']['rows']==[['9007199254740993']]
        # The frame bound is enforced while reading, before parsing an entire line.
        oversized=subprocess.run([binary,'--workspace',str(workspace),'session'],input='x'*(1024*1024+2),text=True,capture_output=True,timeout=10)
        assert oversized.returncode!=0 and 'PROTOCOL_FRAME_TOO_LARGE' in oversized.stdout+oversized.stderr
        print(json.dumps({'session_calls':True,'malformed_json_recovery':True,'disconnect_preserves_jobs':True,'cross_entry_read':True,'input_frame_bound':True}))
    finally:
        if process.poll() is None:process.kill();process.wait()
        if coordinator:
            try:os.kill(coordinator,signal.SIGTERM)
            except ProcessLookupError:pass
