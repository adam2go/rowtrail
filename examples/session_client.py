"""Optional Python standard-library bridge to one native RowTrail session."""
import json
import subprocess
import uuid

class RowTrail:
    def __init__(self, binary='rowtrail', workspace='.rowtrail'):
        self.process=subprocess.Popen([binary,'--workspace',workspace,'session'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)

    def call(self, method, params, idempotency_key=None):
        request={'api_version':'1','request_id':uuid.uuid4().hex,'method':method,'params':params}
        if idempotency_key is not None:request['idempotency_key']=idempotency_key
        self.process.stdin.write(json.dumps(request)+'\n')
        self.process.stdin.flush()
        line=self.process.stdout.readline(1024*1024+2)
        if not line:raise RuntimeError('RowTrail connection closed; inspect workspace/logs')
        response=json.loads(line)
        if not response['ok']:raise RuntimeError(response['error'])
        return response['result']

    def __enter__(self):return self
    def __exit__(self, *_):
        self.process.stdin.close()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
