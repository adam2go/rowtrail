"""Optional Python standard-library bridge to one native RowTrail session."""
import json
import subprocess
import time
import uuid
from decimal import Decimal

class RowTrailError(RuntimeError):
    """Stable error fields; retain the original response for recovery."""
    def __init__(self, error, response=None):
        self.code = error.get("code", "UNKNOWN_ERROR")
        self.retryable = error.get("retryable", False)
        self.details = error.get("details", {})
        self.response = response
        super().__init__(f"{self.code}: {error.get('message', '')}")

class PageBudgetExceeded(RowTrailError):
    """An explicit total budget ended before EOF; cursor can be resumed."""
    def __init__(self, binding, cursor, rows, wire_bytes, pages):
        super().__init__({"code":"PAGE_BUDGET_EXHAUSTED",
            "message":"Pagination stopped before EOF; resume explicitly or increase the total budget",
            "details":{"binding":binding,"next_cursor":cursor,"rows":rows,"wire_bytes":wire_bytes,"pages":pages}})

class JobNotCompleted(RowTrailError):
    """Keep the full durable response, including recoverable partial results."""
    def __init__(self, response):
        job = response['job']
        super().__init__(job.get('error') or {'code':'JOB_NOT_COMPLETED',
            'message':f"Job {job['id']} is {job['state']}"}, response)


class RowTrail:
    def __init__(self, binary='rowtrail', workspace='.rowtrail'):
        self.binary = binary
        self.usable = True
        self.last_response_bytes = 0
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
            self.last_response_bytes = len(line.rstrip("\n").encode())
            response=json.loads(line)
            if (not isinstance(response, dict) or response.get('request_id') != request['request_id']
                    or response.get('api_version') != '1' or type(response.get('ok')) is not bool):
                raise ConnectionError(f'Mismatched RowTrail response; inspect durable state: {response}')
        except (OSError, ValueError, ConnectionError):
            self.usable = False
            self.process.kill()
            self.process.wait()
            raise
        if not response['ok']:
            if response['error'].get('details', {}).get('session_unusable'):
                self.usable = False
            raise RowTrailError(response['error'], response)
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
        if 'job' in value and value['job'].get('prepared') and value['job']['state'] == 'completed':
            value = value['job']['prepared']
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
    def rows(response, *, numeric_policy=None):
        """Return only an already-included complete, exact, untruncated answer.

        Keep the response/observation for schema and provenance. No implicit read,
        pagination, conversion to float, or reinterpretation of partial coverage.
        Use observe()/call('read', ...) explicitly for other observation modes.
        Exactness here concerns sampling, not arbitrary-precision arithmetic.
        numeric_policy can require a declared policy; it does not certify other
        SQL arithmetic or the original inputs.
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
        if numeric_policy is not None and quality.get('numeric', {}).get('policy') != numeric_policy:
            raise ValueError('Required numeric policy is missing; legacy results are not recertified')
        return observation['rows']

    def _completed(self, response, timeout):
        response = self.finish(response, timeout)
        if response['job']['state'] != 'completed':
            raise JobNotCompleted(response)
        return response

    def prepare(self, source, *, execution=None, timeout=30, idempotency_key=None):
        """Explicit CSV/TSV conversion; binding(response) returns the ready dataset."""
        binding = self.binding(source)
        if 'dataset_ref' not in binding:
            raise ValueError('prepare requires a dataset manifest')
        return self._completed(self.call('prepare', {'source':binding,
            'execution':{'wait_ms':1000, **(execution or {})}}, idempotency_key), timeout)

    def inspect(self, source, *, checks=('schema',), columns=(), budget=None,
                execution=None, timeout=30, idempotency_key=None, **options):
        """Schema is metadata-only; scanning checks submit and wait for one job."""
        binding = self.binding(source)
        target = {'ref':binding['manifest_ref']} if 'dataset_ref' in binding else {
            'ref':binding['result_ref'], 'revision':binding['revision']}
        response = self.call('inspect', {**target, 'checks':list(checks), 'columns':list(columns),
            'budget':{'max_rows':100,'max_bytes':8192, **(budget or {})},
            **({'execution':{'wait_ms':1000,'preview':'none',**execution}} if execution is not None else {}),
            **options}, idempotency_key)
        if 'job' not in response:
            return response
        response = self._completed(response, timeout)
        if response.get('observation') is None:
            try:
                response['observation'] = self.call('read', {**self.binding(response),
                    'max_rows':100,'max_bytes':8192, **(budget or {})})
            except Exception as error:
                error.response = response
                raise
        return response

    def export(self, source, destination, *, format='parquet', execution=None,
               timeout=30, idempotency_key=None, allow_nonfinal=False, allow_estimate=False):
        """Export one fixed result revision; never collect it in Python memory."""
        binding = self.binding(source)
        if 'result_ref' not in binding:
            raise ValueError('export requires a fixed result revision')
        return self._completed(self.call('export', {**binding, 'destination':str(destination),
            'format':format, 'allow_nonfinal':allow_nonfinal, 'allow_estimate':allow_estimate,
            'execution':{'wait_ms':1000, **(execution or {})}}, idempotency_key), timeout)

    def find(self, label, *, kind='result', max_bytes=8192):
        """One bounded exact-label lookup; zero, duplicate or unusable is an error.

        No newest/first-match guess. Catalog validity is stored metadata; the
        runtime revalidates sources when the fixed binding is actually used.
        """
        if kind not in ('dataset','result'):
            raise ValueError('find kind must be dataset or result')
        page = self.call('workspace', {'action':'summary','kind':kind,'label':label,
            'limit':2,'max_bytes':max_bytes})
        items = page['items']
        code = ('LABEL_NOT_FOUND' if not items else
                'LABEL_AMBIGUOUS' if len(items) != 1 or page['next_cursor'] else None)
        if code:
            raise RowTrailError({'code':code,'message':f'Expected one {kind} with label {label!r}',
                'details':{'label':label,'kind':kind}}, page)
        item = items[0]
        if item['stored_validity'] != 'valid' or not item.get('binding'):
            raise RowTrailError({'code':'RESULT_UNAVAILABLE','message':'Matching entry is expired, invalid or unreadable'},page)
        if kind == 'result' and not item['quality']['final_for_request']:
            raise RowTrailError({'code':'RESULT_NOT_FINAL','message':'Matching result is a partial revision; select it explicitly if intended'},page)
        return item

    def pages(self, source, *, max_rows, max_bytes, max_pages, columns=(),
              page_rows=1000, page_bytes=65536, cursor=None):
        """Yield bounded page responses, preserving schema/quality/fixed revision.

        max_rows/max_bytes/max_pages are TOTAL limits, including the complete
        UTF-8 response envelopes for byte accounting. EOF ends normally; hitting
        a limit with more data raises PageBudgetExceeded with the resume cursor.
        No full table collection or hidden retry. Stop iteration early if wanted.
        """
        for name, value in (('max_rows',max_rows),('max_bytes',max_bytes),('max_pages',max_pages),
                            ('page_rows',page_rows),('page_bytes',page_bytes)):
            if type(value) is not int or value <= 0:
                raise ValueError(f'{name} must be a positive integer')
        if not 1 <= page_rows <= 10000 or not 1024 <= page_bytes <= 1048064:
            raise ValueError('page_rows must be 1..10000; page_bytes must be 1024..1048064')
        binding = self.binding(source)
        if 'result_ref' not in binding:
            raise ValueError('pages requires a fixed result revision')
        rows = wire_bytes = pages = 0
        while True:
            remaining = max_bytes - wire_bytes
            if rows >= max_rows or pages >= max_pages or remaining < 1024:
                raise PageBudgetExceeded(binding,cursor,rows,wire_bytes,pages)
            page = self.call('read', {**binding, 'columns':list(columns),
                'max_rows':min(page_rows,max_rows-rows),'max_bytes':min(page_bytes,remaining),
                **({'cursor':cursor} if cursor else {})})
            rows += len(page['rows']); wire_bytes += self.last_response_bytes; pages += 1
            next_cursor = page['next_cursor']
            if page['presentation']['has_more'] and (not next_cursor or next_cursor == cursor):
                raise RowTrailError({'code':'INVALID_CURSOR','message':'Pagination made no progress'},page)
            cursor = next_cursor
            yield page
            if not page['presentation']['has_more']:
                return

    @staticmethod
    def typed_rows(response, *, numeric_policy=None):
        """Decode integer/Decimal strings losslessly; preserve dates and nested values.

        This returns a NEW bounded list. Original rows/schema/quality stay intact;
        Python Decimal construction never passes through float.
        """
        rows = RowTrail.rows(response, numeric_policy=numeric_policy)
        fields = response['observation']['schema']
        def convert(value, field):
            kind = field['type']
            if value is None: return None
            if kind.startswith(('Int','UInt')): return int(value)
            if kind.startswith('Decimal'): return Decimal(value)
            return value
        return [[convert(value,field) for value,field in zip(row,fields)] for row in rows]

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
