"""Optional Python standard-library bridge to one native RowTrail session."""
import json
import subprocess
import time
import uuid

class RowTrail:
    def __init__(self, binary='rowtrail', workspace='.rowtrail'):
        self.binary = binary
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

    def schema(self, method):
        """Discover one request contract locally, without starting a runtime."""
        return json.loads(subprocess.check_output([str(self.binary), 'schema', method]))

    def finish(self, response, timeout=30):
        """Wait mechanically for at most timeout seconds; never replay a mutation.

        A deadline returns the last known job state. It does not cancel the job or
        imply success. Transport errors propagate so the caller can reconnect.
        """
        deadline = time.monotonic() + max(0, timeout)
        while response['job']['state'] in ('queued', 'running', 'stopping'):
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0: break
            response = self.call('control', {'action': 'wait', 'ref': response['job']['id'], 'wait_ms': min(1000, remaining)})
        return response

    @staticmethod
    def observe(response):
        """Optional model-facing projection; keep the complete response in code.

        Preserve typed rows, quality, validity, truncation and fixed revisions.
        Operational timing/plan details remain in the original job response.
        """
        job = response['job']
        revision = response.get('readable_revision')
        return {
            'job_id': job['id'], 'state': job['state'],
            'binding': {'result_ref': job['result_ref'], 'revision': revision} if revision else None,
            'observation': response.get('observation'),
            'observation_omitted': response.get('observation_omitted'),
            'error': job.get('error'), 'next_actions': response.get('next_actions', []),
        }

    def __enter__(self):return self
    def __exit__(self, *_):
        self.process.stdin.close()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
