"""Optional Python standard-library bridge to one native RowTrail session."""
import json
import subprocess
import time
import uuid

class JobNotCompleted(RuntimeError):
    """Keep the full durable response, including recoverable partial results."""
    def __init__(self, response):
        self.response = response
        job = response['job']
        super().__init__(f"Job {job['id']} is {job['state']}: {job.get('error')}")

class RowTrail:
    def __init__(self, binary='rowtrail', workspace='.rowtrail'):
        self.binary = binary
        self.usable = True
        self.process=subprocess.Popen([binary,'--workspace',workspace,'session'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)

    def call(self, method, params, idempotency_key=None):
        if not self.usable:
            raise ConnectionError('Session is unusable after transport failure; reconnect and inspect durable state')
        request={'api_version':'1','request_id':uuid.uuid4().hex,'method':method,'params':params}
        if idempotency_key is not None:request['idempotency_key']=idempotency_key
        encoded = json.dumps(request, ensure_ascii=False, separators=(',', ':'))
        if len(encoded.encode()) > 1024 * 1024:
            raise ValueError('Request exceeds the 1 MiB frame limit')
        try:
            self.process.stdin.write(encoded+'\n')
            self.process.stdin.flush()
            line=self.process.stdout.readline(1024*1024+2)
            if not line.endswith('\n') or len(line.rstrip('\n').encode()) > 1024*1024:
                raise ConnectionError('Closed or invalid RowTrail frame; inspect durable state')
            response=json.loads(line)
            if (not isinstance(response, dict) or response.get('request_id') != request['request_id']
                    or response.get('api_version') != '1' or type(response.get('ok')) is not bool):
                raise ConnectionError(f'Mismatched RowTrail response; inspect durable state: {response}')
        except (OSError, ValueError, ConnectionError):
            self.usable = False
            self.process.kill()
            self.process.wait()
            raise
        if not response['ok']:raise RuntimeError(response['error'])
        return response['result']

    def schema(self, method):
        """Discover one request contract locally, without starting a runtime."""
        return json.loads(subprocess.check_output([str(self.binary), 'schema', method]))

    def open(self, source, **options):
        """Metadata-only open; retain the response for both fields and binding."""
        return self.call('open', {'source': str(source), **options})

    @staticmethod
    def binding(value):
        """Accept an open/query/read/catalog response or an explicit fixed binding.

        This selects an immutable revision; it does not certify validity, complete
        source coverage, or success. The runtime validates it when it is used.
        """
        if isinstance(value.get('binding'), dict):
            value = value['binding']
        if 'dataset_ref' in value and 'manifest_ref' in value:
            return {key: value[key] for key in ('dataset_ref', 'manifest_ref')}
        if 'job' in value:
            value = {'result_ref': value['job'].get('result_ref'),
                     'revision': value.get('readable_revision')}
        revision = value.get('revision')
        if value.get('result_ref') and type(revision) is int and revision > 0:
            return {'result_ref': value['result_ref'], 'revision': revision}
        raise ValueError('A dataset manifest or a readable fixed result revision is required')

    def query(self, sql, bindings=None, *, parameters=(), execution=None,
              timeout=30, idempotency_key=None, label=None):
        """Submit once and wait mechanically, returning the complete job response.

        Defaults suit final-answer SQL. Use call('query', ...) for immediate
        asynchronous acceptance or previews. A helper deadline never cancels or
        replays a job; JobNotCompleted.response retains its ID and full state.
        """
        response = self.call('query', {
            'sql': sql,
            'bindings': {name: self.binding(value) for name, value in (bindings or {}).items()},
            'parameters': list(parameters),
            **({'label': label} if label is not None else {}),
            'execution': {'wait_ms': 1000, 'preview': 'none', **(execution or {})},
        }, idempotency_key=idempotency_key)
        response = self.finish(response, timeout=timeout)
        if response['job']['state'] != 'completed':
            raise JobNotCompleted(response)
        if response.get('observation') is None:
            # A wait reply carries durable state without rows. Fetch one bounded
            # page only when needed; an included observation is never reread.
            budget = {'max_rows': 100, 'max_bytes': 8192,
                      **(execution or {}).get('output', {})}
            try:
                response['observation'] = self.call('read', {**self.binding(response), **budget})
            except Exception as error:
                error.response = response
                raise
        return response

    @staticmethod
    def rows(response):
        """Return only an already-included complete, exact, untruncated answer.

        Keep the response/observation for schema and provenance. No implicit read,
        pagination, conversion to float, or reinterpretation of partial coverage.
        Use observe()/call('read', ...) explicitly for other observation modes.
        """
        observation = response.get('observation')
        if response['job']['state'] != 'completed':
            raise JobNotCompleted(response)
        if not observation:
            raise ValueError('No included observation; read the fixed binding within an explicit budget')
        quality = observation['quality']
        if (observation['validity'] != 'valid' or quality['accuracy'] != 'exact'
                or quality['coverage']['kind'] != 'complete' or not quality['final_for_request']
                or observation['presentation']['has_more']):
            raise ValueError('Observation is invalid, partial, nonfinal, estimated, or truncated; inspect its full quality')
        return observation['rows']

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
        try:self.process.stdin.close()
        except OSError:pass
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
