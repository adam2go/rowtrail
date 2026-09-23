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
            if response['ok']:
                if 'result' not in response:
                    raise ConnectionError('Successful RowTrail response is missing its result')
            else:
                error = response.get('error')
                if (not isinstance(error, dict) or not isinstance(error.get('code'), str)
                        or not isinstance(error.get('message'), str)
                        or type(error.get('retryable')) is not bool or 'details' not in error):
                    raise ConnectionError('Malformed RowTrail error envelope; inspect durable state')
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
              timeout=30, idempotency_key=None, label=None, provenance=None):
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
            **({'provenance': provenance} if provenance is not None else {}),
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

    def lookup(self, idempotency_key):
        """Read durable acceptance by key without submitting or replaying anything.

        OBJECT_NOT_FOUND means no committed entry was observed by this lookup;
        a still-in-flight caller may commit later. It is not permission to replay.
        """
        return self.call('control', {'action':'lookup','ref':idempotency_key})

    def snapshot(self, source, *, label=None, provenance=None, execution=None,
                 timeout=30, idempotency_key=None):
        """Copy into an independent managed dataset; never register a mutable path."""
        return self._completed(self.call('snapshot', {'source':self.binding(source),
            'label':label, 'provenance':provenance,
            'execution':{'wait_ms':1000, **(execution or {})}}, idempotency_key), timeout)

    def from_parquet(self, path, **options):
        """Open then snapshot a cleaned Parquet result without loading it in Python.

        For recovery across an ambiguous transport failure, use open + snapshot
        explicitly, retaining the binding and snapshot idempotency key.
        """
        return self.snapshot(self.open(path, format='parquet'), **options)

    @staticmethod
    def records(response):
        """Named values from an included exact, complete, untruncated observation."""
        rows = RowTrail.typed_rows(response)
        names = [f['name'] for f in response['observation']['schema']]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate output names; assign unique SQL aliases')
        return [dict(zip(names, row)) for row in rows]

    @staticmethod
    def scalar(response):
        """Exactly one row and column, losslessly typed; no implicit full fetch."""
        rows = RowTrail.typed_rows(response)
        if len(rows) != 1 or len(rows[0]) != 1:
            raise ValueError('Expected exactly one row and one column')
        return rows[0][0]

    def check(self, sql=None, bindings=None, **options):
        return _check(self, sql, bindings, **options)

    def diff(self, before, after, *, keys, **options):
        return _diff(self, before, after, keys=keys, **options)

    def run_recipe(self, recipe, inputs, *, run_dir, **options):
        return _run_recipe(self, recipe, inputs, run_dir=run_dir, **options)

    def pack(self, source, destination, **options):
        return _pack(self, source, destination, **options)

    @staticmethod
    def verify_package(directory, **options):
        return _verify_package(directory, **options)

    def import_package(self, directory, **options):
        return _import_package(self, directory, **options)

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
            if kind in ('Int8','Int16','Int32','Int64','UInt8','UInt16','UInt32','UInt64'): return int(value)
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

# Optional analysis composition. All scans, joins and result storage stay native.
# These helpers never import a dataframe library or execute caller-authored code.
def _fail(code, message, details=None):
    raise RowTrailError({'code':code, 'message':message, 'details':details or {}})


def _identifier(name):
    if not isinstance(name, str) or not name or len(name.encode()) > 1024:
        raise ValueError('Column identifiers must be nonempty strings of at most 1024 UTF-8 bytes')
    return '"' + name.replace('"', '""') + '"'


def _metadata(rt, source):
    value = rt.inspect(source, checks=('schema','provenance'),
                       budget={'max_rows':10000, 'max_bytes':1048064})
    if value.get('next_field_offset') is not None:
        _fail('METADATA_LIMIT', 'Complete schema does not fit the metadata budget')
    if value.get('validity') != 'valid':
        _fail('RESULT_UNAVAILABLE', 'Stored object is invalid or expired', value)
    return value


def _require_final(meta):
    q = meta.get('quality', {})
    if (meta.get('validity') != 'valid' or q.get('accuracy') != 'exact'
            or q.get('coverage',{}).get('kind') != 'complete'
            or q.get('final_for_request') is not True):
        _fail('RESULT_NOT_FINAL', 'Requires a valid, final, exact, complete revision', meta)


def _query_options(execution, samples):
    if type(samples) is not int or not 0 <= samples <= 100:
        raise ValueError('samples must be 0..100')
    return {**(execution or {}), 'preview':'none',
            'output':{'max_rows':samples, 'max_bytes':65536}}


def _check(rt, sql=None, bindings=None, *, forbidden_columns=None, name='check', parameters=(), execution=None,
           samples=10, timeout=30, idempotency_key=None):
    """A read-only SQL query returns violations. Empty complete result means pass.

    The whole violation result is retained within native execution budgets;
    examples alone are bounded. Errors/partial coverage never count as a pass.
    Reuse the same definition in a recipe's kind=check step.
    """
    sql = _assertion_sql(rt,sql,bindings,forbidden_columns)
    response = rt.query(sql, bindings, parameters=parameters, label=name,
        execution=_query_options(execution,samples), timeout=timeout,
        idempotency_key=idempotency_key, provenance={'description':'Assertion: returned rows are violations.'})
    meta = _metadata(rt, response)
    _require_final(meta)
    count = int(meta['row_count'])
    return {'format':'rowtrail.check.v1', 'name':name, 'passed':count == 0,
            'violations':count, 'binding':rt.binding(response),
            'quality':meta['quality'], 'examples':response['observation'],
            'job':response['job'], 'scope_ref':meta['scope_ref'],
            'cost':'full SQL execution; complete violations saved under execution budgets'}


def _diff(rt, before, after, *, keys, samples=10, execution=None, timeout=30):
    """Exact keyed comparison of two complete result revisions, with bounded samples.

    Null/duplicate keys block comparison. Added/removed/type-changed columns are
    schema changes; modified rows compare common same-type non-key columns only.
    NaNs, timestamps and nested values follow the SQL engine's equality semantics.
    """
    if not isinstance(keys, (tuple,list)) or not keys or len(keys)>16 or len(set(keys)) != len(keys):
        raise ValueError('keys must contain 1..16 distinct column names')
    options = _query_options(execution,samples)
    left, right = rt.binding(before), rt.binding(after)
    if 'result_ref' not in left or 'result_ref' not in right:
        raise ValueError('diff requires fixed result revisions; query snapshots first')
    lm, rm = _metadata(rt,left), _metadata(rt,right)
    _require_final(lm); _require_final(rm)
    lf, rf = ({f['name']:f['type'] for f in m['fields']} for m in (lm,rm))
    if len(lf) != len(lm['fields']) or len(rf) != len(rm['fields']):
        raise ValueError('Comparison requires unique column names')
    schema = {'added':[k for k in rf if k not in lf], 'removed':[k for k in lf if k not in rf],
              'changed':{k:{'before':lf[k],'after':rf[k]} for k in lf.keys() & rf.keys() if lf[k] != rf[k]}}
    report = {'format':'rowtrail.diff.v1','before':left,'after':right,'keys':list(keys),
              'schema':schema,'rows':{'before':int(lm['row_count']),'after':int(rm['row_count'])},
              'coverage':'complete fixed revisions; modified rows compare common same-type columns',
              'key_checks':{},'quality':{'before':lm['quality'],'after':rm['quality']}}
    incompatible = [k for k in keys if k not in lf or k not in rf or lf[k] != rf[k]]
    if incompatible:
        return {**report,'status':'blocked','reason':'missing_or_incompatible_keys','columns':incompatible}
    columns = [k for k in lf if k in rf and lf[k]==rf[k] and k not in keys]
    if len(columns)>128:
        _fail('METADATA_LIMIT','Keyed diff supports at most 128 comparable non-key columns')
    count_alias = '__rowtrail_key_count'
    while count_alias in keys:count_alias += '_'
    count_name = _identifier(count_alias)
    key_sql = ','.join(_identifier(k) for k in keys)
    null_sql = ' OR '.join(f'{_identifier(k)} IS NULL' for k in keys)
    for side, binding in [('before',left),('after',right)]:
        groups = f'SELECT {key_sql}, COUNT(*) AS {count_name} FROM t GROUP BY {key_sql}'
        counts = rt.query(f'SELECT COUNT(*) FILTER (WHERE {count_name} > 1) duplicate_keys, '
            f'COUNT(*) FILTER (WHERE {null_sql}) null_keys FROM ({groups}) g', {'t':binding},
            execution={**options,'output':{'max_rows':1,'max_bytes':8192}},timeout=timeout)
        report['key_checks'][side] = rt.records(counts)[0]
        if any(report['key_checks'][side].values()):
            example = rt.query(f'SELECT * FROM ({groups}) g WHERE {count_name}>1 OR {null_sql} LIMIT {samples}',
                {'t':binding},execution=options,timeout=timeout)
            report['key_checks'][side]['examples']=example['observation']
    if any(c['duplicate_keys'] or c['null_keys'] for c in report['key_checks'].values()):
        return {**report,'status':'blocked','reason':'duplicate_or_null_keys'}
    on = ' AND '.join(f'l.{_identifier(k)} = r.{_identifier(k)}' for k in keys)
    # Non-null unique keys are a checked precondition, so no synthetic sentinel
    # column can collide with user data and NULL reliably marks an absent side.
    lk, rk = f'l.{_identifier(keys[0])}', f'r.{_identifier(keys[0])}'
    added, deleted = f'{lk} IS NULL', f'{rk} IS NULL'
    changed = {k:f'(l.{_identifier(k)} IS DISTINCT FROM r.{_identifier(k)})' for k in columns}
    modified = f'{lk} IS NOT NULL AND {rk} IS NOT NULL AND (' + (' OR '.join(changed.values()) or 'FALSE') + ')'
    join = f'FROM l FULL OUTER JOIN r ON {on}'
    expressions = [f'COUNT(*) FILTER (WHERE {cond}) AS {_identifier(name)}'
        for name,cond in [('added',added),('deleted',deleted),('modified',modified)]]
    expressions += [f'COUNT(*) FILTER (WHERE {lk} IS NOT NULL AND {rk} IS NOT NULL AND ({cond})) AS "c{i}"'
                    for i,(k,cond) in enumerate(changed.items())]
    summary = rt.query('SELECT '+','.join(expressions)+' '+join, {'l':left,'r':right},
        execution={**options,'output':{'max_rows':1,'max_bytes':65536}},timeout=timeout)
    counts = rt.records(summary)[0]
    report['rows'].update({k:counts[k] for k in ('added','deleted','modified')})
    report['changed_fields']={k:counts[f'c{i}'] for i,k in enumerate(columns)}
    report['comparison_columns']=columns
    report['summary_job']=summary['job']
    # Bounded deterministic examples with a field mapping; duplicate output names
    # and collisions with original columns are impossible.
    projection = [f"CASE WHEN {added} THEN 'added' WHEN {deleted} THEN 'deleted' ELSE 'modified' END change"]
    mapping = []
    for i,k in enumerate(keys+columns if isinstance(keys,list) else list(keys)+columns):
        projection += [f'l.{_identifier(k)} AS "before_{i}"', f'r.{_identifier(k)} AS "after_{i}"']
        mapping.append({'column':k,'before':f'before_{i}','after':f'after_{i}'})
    order = ','.join(f'COALESCE(l.{_identifier(k)},r.{_identifier(k)})' for k in keys)
    examples = rt.query('SELECT '+','.join(projection)+' '+join+
        f' WHERE {added} OR {deleted} OR ({modified}) ORDER BY {order} LIMIT {samples}',
        {'l':left,'r':right},execution=options,timeout=timeout)
    report.update(status='compared',examples=examples['observation'],example_fields=mapping,
                  samples_limited=report['rows']['added']+report['rows']['deleted']+report['rows']['modified']>samples)
    return report


def _json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()


def _atomic_json(path, value):
    import os
    from pathlib import Path
    path = Path(path)
    data = _json_bytes(value)
    if len(data)>8*1024*1024:
        _fail('METADATA_LIMIT','Analysis metadata exceeds 8 MiB')
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('xb') as out:
            out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


def _load_json(value, limit=8*1024*1024):
    from pathlib import Path
    if isinstance(value,dict):
        raw=_json_bytes(value)
    else:
        with Path(value).open('rb') as f:raw=f.read(limit+1)
    if len(raw)>limit:
        _fail('METADATA_LIMIT','Analysis metadata exceeds byte limit')
    def unique(pairs):
        out={}
        for key,val in pairs:
            if key in out:raise ValueError('Duplicate JSON key: '+key)
            out[key]=val
        return out
    return json.loads(raw,object_pairs_hook=unique,
        parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Non-finite JSON number')))


def _validate_recipe(recipe):
    import re
    r=_load_json(recipe,1024*1024)
    if r.get('format') != 'rowtrail.recipe.v1':raise ValueError('Unsupported recipe format')
    inputs=r.get('inputs');steps=r.get('steps')
    if (not isinstance(inputs,list) or len(inputs)>64 or len(set(inputs))!=len(inputs)
            or not isinstance(steps,list) or not 1<=len(steps)<=64):
        raise ValueError('Recipe needs unique inputs and 1..64 ordered steps')
    available={f'input:{name}' for name in inputs}
    seen=set()
    for step in steps:
        name=step.get('id')
        if not isinstance(name,str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}',name) or name in seen:
            raise ValueError('Step IDs must be distinct identifiers of at most 64 characters')
        if (step.get('kind','query') not in ('query','check') or
                not (isinstance(step.get('sql'),str) or
                     step.get('kind')=='check' and isinstance(step.get('forbidden_columns'),list))):
            raise ValueError('Each step needs SQL and kind=query or check')
        bindings=step.get('bindings',{})
        if (not isinstance(bindings,dict) or any(not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}',k) for k in bindings)
                or any(v not in available for v in bindings.values())):
            raise ValueError('Step bindings must reference named inputs or earlier steps')
        if not isinstance(step.get('parameters',[]),list):raise ValueError('Step parameters must be a list')
        seen.add(name);available.add('step:'+name)
    return r


def _run_recipe(rt, recipe, inputs, *, run_dir, parameters=None, execution=None,
                timeout=30, stop_on_failure=True):
    """Explicit sequential SQL DAG run with a separate fsynced run directory.

    No scheduling, code evaluation, retries or hidden resume. Input and step names
    resolve to fixed bindings; named parameters carry native typed JSON values.
    A failed/uncertain step retains its idempotency key for deliberate recovery.
    """
    from pathlib import Path
    r=_validate_recipe(recipe)
    if set(inputs)!=set(r['inputs']):raise ValueError('Provide exactly the recipe input names')
    values={**r.get('parameters',{}),**(parameters or {})}
    for step in r['steps']:
        for p in step.get('parameters',[]):
            if isinstance(p,str) and p not in values:raise ValueError('Missing typed parameter '+p)
            if not isinstance(values[p] if isinstance(p,str) else p,dict):
                raise ValueError('Parameters must be native typed parameter objects')
    resolved={'input:'+k:rt.binding(v) for k,v in inputs.items()}
    identities={k:_metadata(rt,v) for k,v in inputs.items()}
    for k,v in inputs.items():
        if 'result_ref' in rt.binding(v):_require_final(identities[k])
    directory=Path(run_dir).absolute();directory.mkdir(parents=True,exist_ok=False)
    record={'format':'rowtrail.run.v1','run_id':uuid.uuid4().hex,'status':'running',
            'recipe':r,'parameters':values,'inputs':{k:rt.binding(v) for k,v in inputs.items()},
            'input_metadata':identities,'steps':[]}
    path=directory/'run.json';_atomic_json(path,record)
    failed=False
    for step in r['steps']:
        entry={'id':step['id'],'kind':step.get('kind','query'),'state':'submitting',
               'idempotency_key':record['run_id']+':'+step['id']}
        record['steps'].append(entry);_atomic_json(path,record)
        try:
            bindings={k:resolved[v] for k,v in step.get('bindings',{}).items()}
            sql=_assertion_sql(rt,step.get('sql'),bindings,step.get('forbidden_columns'))
            response=rt.call('query',{'sql':sql,
                'bindings':bindings,
                'parameters':[values[p] if isinstance(p,str) else p for p in step.get('parameters',[])],
                'label':step['id'],'provenance':{'description':step.get('description','')},
                'execution':{**_query_options(execution,10),'wait_ms':0}},entry['idempotency_key'])
            entry.update(state='accepted',response=response);_atomic_json(path,record)
            response=rt._completed(response,timeout)
            binding=rt.binding(response);meta=_metadata(rt,binding);_require_final(meta)
            entry.update(state='completed',binding=binding,metadata=meta,response=response)
            resolved['step:'+step['id']]=binding
            if entry['kind']=='check':
                entry['violations']=int(meta['row_count']);entry['passed']=entry['violations']==0
                entry['examples']=rt.call('read',{**binding,'max_rows':10,'max_bytes':65536})
                failed |= not entry['passed']
                if failed and stop_on_failure:
                    record['status']='checks_failed';_atomic_json(path,record);return record
            _atomic_json(path,record)
        except Exception as error:
            entry.update(state='uncertain' if isinstance(error,ConnectionError) or not rt.usable else 'failed',
                error={'code':getattr(error,'code',type(error).__name__),'message':str(error),
                       'details':getattr(error,'details',{}),'response':getattr(error,'response',None)})
            record['status']=entry['state'];_atomic_json(path,record)
            error.run_record=record;error.run_path=str(path)
            raise
    record['status']='checks_failed' if failed else 'completed';_atomic_json(path,record)
    return record


def _file_hash(path):
    import hashlib
    with path.open('rb') as f:
        digest=hashlib.sha256()
        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def _pack(rt, source, destination, *, include_inputs=False, notes='', max_nodes=64,
          max_bytes=1024*1024*1024, execution=None, timeout=30):
    """Export one result branch as manifest.json, report.md and Parquet files.

    Directory creation is exclusive; manifest publication is the completion
    marker. Interrupted exports retain failure.json/job IDs and must not be
    imported. Native jobs remain retained. No original file is copied by default.
    """
    import os
    from pathlib import Path
    if type(max_nodes) is not int or not 1<=max_nodes<=64 or type(max_bytes) is not int or max_bytes<=0:
        raise ValueError('max_nodes must be 1..64 and max_bytes a positive integer')
    if not isinstance(notes,str) or len(notes.encode())>16384:raise ValueError('notes limit is 16384 UTF-8 bytes')
    root_binding=rt.binding(source)
    if 'result_ref' not in root_binding:raise ValueError('pack requires a fixed result branch')
    directory=Path(destination).absolute();directory.mkdir(parents=True,exist_ok=False)
    nodes=[];known={};visiting=set();used=0
    manifest={'format':'rowtrail.package.v1','package_id':uuid.uuid4().hex,'root':None,
              'notes':notes,'include_inputs':bool(include_inputs),'nodes':nodes,
              'verification':'SHA-256 verifies included bytes, not authorship, business correctness or original data freshness'}
    def visit(binding):
        nonlocal used
        key=json.dumps(binding,sort_keys=True)
        if key in visiting:raise ValueError('Cyclic stored branch')
        if key in known:return known[key]
        if len(nodes)>=max_nodes:_fail('METADATA_LIMIT','Branch exceeds max_nodes')
        node_id=f'n{len(nodes):03d}';known[key]=node_id;visiting.add(key)
        meta=_metadata(rt,binding)
        is_result='result_ref' in binding
        if is_result:_require_final(meta)
        node={'id':node_id,'kind':'result' if is_result else 'input','binding':binding,
              'metadata':meta,'dependencies':{},'payload':None}
        nodes.append(node)
        if is_result:
            try:
                node['preview']=rt.call('read',{**binding,'max_rows':5,'max_bytes':8192})
            except RowTrailError as error:
                if error.code!='OUTPUT_BUDGET_TOO_SMALL':raise
                node['preview']={'omitted':'output_budget','rows':None}
            scope=rt.call('inspect',{'ref':meta['scope_ref'],'checks':['schema'],
                                     'budget':{'max_rows':10000,'max_bytes':1048064}})
            node['scope']=scope
            for alias,dep in (scope.get('inputs') or {}).items():
                node['dependencies'][alias]=visit(rt.binding(dep))
        elif meta.get('scope_ref'):
            # A managed snapshot is an intentional independence boundary. Retain
            # its generating scope as provenance, not a live external dependency.
            node['origin_scope']=rt.call('inspect',{'ref':meta['scope_ref'],'checks':['schema'],
                'budget':{'max_rows':10000,'max_bytes':1048064}})
        if is_result or include_inputs:
            remaining=max_bytes-used
            if remaining<=0:_fail('PACKAGE_BUDGET_EXHAUSTED','Package payload byte limit reached')
            limits={**(execution or {}),'result_bytes':min(remaining,(execution or {}).get('result_bytes',512*1024*1024))}
            result=binding
            if not is_result:
                result=rt.query('SELECT * FROM source',{'source':binding},
                    execution={**limits,'output':{'max_rows':0,'max_bytes':65536}},timeout=timeout)
                _require_final(_metadata(rt,result))
            file=directory/(node_id+'.parquet')
            exported=rt.export(result,file,execution=limits,timeout=timeout)
            size=file.stat().st_size;used+=size
            if used>max_bytes:_fail('PACKAGE_BUDGET_EXHAUSTED','Export exceeded package payload limit')
            node['payload']={'file':file.name,'bytes':size,'sha256':_file_hash(file),'complete':True,
                             'export_job_id':exported['job']['id']}
            # The standalone export sidecar is redundant: its quality and identity
            # are already in the bounded package manifest. Keep and list it if present.
            sidecar=file.with_suffix(file.suffix+'.rowtrail.json')
            if sidecar.exists():
                size=sidecar.stat().st_size;used+=size
                if used>max_bytes:_fail('PACKAGE_BUDGET_EXHAUSTED','Package sidecars exceed byte limit')
                node['payload']['sidecar']={'file':sidecar.name,'bytes':size,'sha256':_file_hash(sidecar)}
        visiting.remove(key)
        return node_id
    try:
        manifest['root']=visit(root_binding)
        manifest['payload_bytes']=used
        manifest['recomputation']='all input payloads included' if include_inputs else 'explicit original input mappings required'
        lines=['# RowTrail analysis package','',f"Package `{manifest['package_id']}` · root `{manifest['root']}`",'',
               'Included Parquet results can be verified and imported for follow-up queries.',
               'Original recomputation requires all inputs and an explicit recipe run. No SQL or code runs on import.',
               'Checksums detect changed bytes; they do not certify the author, claims or original source freshness.','',
               '## Author notes','']
        # Indent user text/SQL to avoid interpreting embedded HTML or Markdown as
        # report structure. Reports are plain Markdown with no active content.
        lines += ['    '+line for line in notes.splitlines()]+['','## Branch','']
        for node in nodes:
            meta=node['metadata'];scope=node.get('scope',{})
            lines += [f"### {node['id']} ({node['kind']})",'',
                f"Rows: {meta.get('row_count','not counted')}. Payload: {node['payload']['file'] if node['payload'] else 'not included'}.",
                '','Quality and provenance (declarations; see manifest for full identity):','']
            details={'quality':meta.get('quality'), 'verification':meta.get('verification'),
                     'provenance':scope.get('provenance',meta.get('provenance')),
                     'parameters':scope.get('parameters'),'dependencies':node['dependencies']}
            lines += ['    '+line for line in json.dumps(details,ensure_ascii=False,indent=2).splitlines()]
            if node.get('preview'):
                lines += ['','Bounded result preview (presentation.has_more is separate from full payload coverage):','']
                preview={k:v for k,v in node['preview'].items() if k in ('schema','rows','presentation','omitted')}
                lines += ['    '+line for line in json.dumps(preview,ensure_ascii=False,indent=2).splitlines()]
            if scope.get('sql'):lines += ['','Executed SQL:','']+['    '+line for line in scope['sql'].splitlines()]
            lines.append('')
        report='\n'.join(lines)+'\n'
        if len(report.encode())>8*1024*1024:_fail('METADATA_LIMIT','Report exceeds 8 MiB')
        if used+len(report.encode())>max_bytes:_fail('PACKAGE_BUDGET_EXHAUSTED','Report exceeds package byte limit')
        with (directory/'report.md').open('x',encoding='utf-8') as f:
            f.write(report);f.flush();os.fsync(f.fileno())
        manifest['report']={'file':'report.md','bytes':len(report.encode()),'sha256':_file_hash(directory/'report.md')}
        _atomic_json(directory/'manifest.json',manifest)
        return manifest
    except Exception as error:
        _atomic_json(directory/'failure.json',{'status':'incomplete','manifest':manifest,
            'error':str(error),'response':getattr(error,'response',None)})
        error.package_path=str(directory)
        raise


def _verify_package(directory, *, max_bytes=1024*1024*1024, max_nodes=64):
    """Offline bounded byte verification; never opens recorded original paths.

    The directory may be shared as-is or compressed by the caller. Archives are
    not extracted here. Paths must be immediate regular files, never symlinks.
    Payload checksums are integrity checks, not proof of semantic correctness.
    """
    import re,stat
    from pathlib import Path
    directory=Path(directory).absolute()
    if type(max_bytes) is not int or max_bytes<=0 or type(max_nodes) is not int or not 1<=max_nodes<=64:
        raise ValueError('Positive max_bytes and max_nodes=1..64 required')
    if directory.is_symlink() or not directory.is_dir():raise ValueError('Package must be a real directory')
    def safe_file(name):
        if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9_.-]{1,128}',name) or name in ('.','..'):
            raise ValueError('Invalid package file name')
        path=directory/name
        if not stat.S_ISREG(path.lstat().st_mode):raise ValueError('Package payload must be a regular file')
        return path
    manifest=_load_json(safe_file('manifest.json'))
    if manifest.get('format')!='rowtrail.package.v1':raise ValueError('Unsupported package format')
    nodes=manifest.get('nodes')
    if not isinstance(nodes,list) or not 1<=len(nodes)<=max_nodes:raise ValueError('Package node count limit')
    ids={n.get('id') for n in nodes}
    if len(ids)!=len(nodes) or manifest.get('root') not in ids:raise ValueError('Duplicate nodes or missing root')
    seen_files={'manifest.json'};total=0;indexed={n['id']:n for n in nodes}
    def verify_file(item):
        nonlocal total
        name=item['file']
        if name in seen_files:raise ValueError('Duplicate package file')
        seen_files.add(name)
        path=safe_file(name);size=path.stat().st_size
        if type(item.get('bytes')) is not int or size!=item['bytes']:raise ValueError('Package size mismatch')
        total+=size
        if total>max_bytes:_fail('PACKAGE_BUDGET_EXHAUSTED','Package files exceed max_bytes')
        if _file_hash(path)!=item.get('sha256'):_fail('PACKAGE_CORRUPT','Package checksum mismatch',{'file':name})
    for node in nodes:
        if not re.fullmatch('n[0-9]{3}',node['id']) or node.get('kind') not in ('input','result'):
            raise ValueError('Invalid node identifier or kind')
        deps=node.get('dependencies')
        if not isinstance(deps,dict) or any(v not in ids for v in deps.values()):raise ValueError('Missing dependency')
        if node['kind']=='result':
            _require_final(node['metadata'])
            scope=node.get('scope',{})
            if not isinstance(scope.get('sql'),str) or set(scope.get('inputs') or {})!=set(deps):
                raise ValueError('Missing SQL or dependency mismatch')
        payload=node.get('payload')
        if payload:
            if payload.get('file')!=node['id']+'.parquet':raise ValueError('Invalid payload name')
            if payload.get('complete') is not True:raise ValueError('Only complete payloads can be imported')
            verify_file(payload)
            if payload.get('sidecar'):verify_file(payload['sidecar'])
        elif node['kind']=='result':raise ValueError('Result node requires a payload')
    verify_file(manifest['report'])
    visiting=set();visited=set()
    def visit(node):
        if node in visiting:raise ValueError('Cyclic package dependency')
        if node in visited:return
        visiting.add(node)
        for dep in indexed[node]['dependencies'].values():visit(dep)
        visiting.remove(node);visited.add(node)
    visit(manifest['root'])
    if visited!=ids:raise ValueError('Package contains unreachable nodes')
    return {'status':'verified_bytes','manifest':manifest,'verified_bytes':total,
            'original_sources_checked':False,'sql_recomputed':False,'authorship_verified':False,
            'missing_inputs':[n['id'] for n in nodes if n['kind']=='input' and not n['payload']]}


def _package_recipe(manifest):
    """Translate the recorded SQL DAG; source mappings remain explicit."""
    indexed={n['id']:n for n in manifest['nodes']};ordered=[];seen=set()
    def visit(node):
        if node in seen:return
        for dep in indexed[node]['dependencies'].values():visit(dep)
        seen.add(node);ordered.append(indexed[node])
    visit(manifest['root'])
    return {'format':'rowtrail.recipe.v1',
        'inputs':[n['id'] for n in ordered if n['kind']=='input'],
        'steps':[{'id':n['id'],'sql':n['scope']['sql'],
                  'parameters':n['scope'].get('parameters') or [],
                  'description':(n['scope'].get('provenance') or {}).get('description',''),
                  'bindings':{alias:('input:' if indexed[dep]['kind']=='input' else 'step:')+dep
                              for alias,dep in n['dependencies'].items()}}
                 for n in ordered if n['kind']=='result']}


def _import_package(rt, directory, *, max_bytes=1024*1024*1024, execution=None, timeout=30):
    """Verify and independently snapshot payloads. Never rerun recorded SQL/code.

    Each completed snapshot survives a later failure. Import is not a transaction
    spanning multiple native jobs. The raised exception retains completed mapping.
    Returned recipe is inert JSON; run it explicitly with required inputs.
    """
    from pathlib import Path
    import tempfile
    verified=_verify_package(directory,max_bytes=max_bytes)
    manifest=verified['manifest'];mapping={}
    try:
        for node in manifest['nodes']:
            payload=node['payload']
            if not payload:continue
            path=Path(directory).absolute()/payload['file']
            # Recheck immediately before native open. Native execution separately
            # validates the frozen file identity before and after its read.
            with tempfile.TemporaryDirectory(prefix='rowtrail-import-') as td:
                private=Path(td)/'payload.parquet'
                _copy_verified(path,private,payload)
                snap=rt.from_parquet(private,label='import:'+node['id'],
                    provenance={'origin':'rowtrail.package:'+str(manifest['package_id']),
                                'description':'Imported '+node['id']+'; source SQL is recorded, not recomputed.'},
                    execution=execution,timeout=timeout)
            binding=rt.binding(snap);mapping[node['id']]=binding
            actual=_metadata(rt,binding)
            if actual['fields']!=node['metadata']['fields']:
                _fail('PACKAGE_CORRUPT','Payload schema differs from recorded schema',{'node':node['id']})
            # A constant-memory native count validates the declaration without
            # collecting rows or trusting editable manifest metadata.
            count=rt.scalar(rt.query('SELECT COUNT(*) FROM t',{'t':binding},execution=execution,timeout=timeout))
            if node['kind']=='result' and count!=int(node['metadata']['row_count']):
                _fail('PACKAGE_CORRUPT','Payload row count differs from recorded result',{'node':node['id']})
        return {'format':'rowtrail.import.v1','status':'imported','binding':mapping[manifest['root']],
                'mapping':mapping,'verification':{k:v for k,v in verified.items() if k!='manifest'},
                'provenance':manifest,'recipe':_package_recipe(manifest)}
    except Exception as error:
        error.imported_bindings=mapping
        raise


def _copy_verified(source, destination, expected):
    import hashlib,os,stat
    digest=hashlib.sha256();size=0
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as inp, destination.open('xb') as out:
        if not stat.S_ISREG(os.fstat(inp.fileno()).st_mode):raise ValueError('Expected regular payload')
        for chunk in iter(lambda:inp.read(1024*1024),b''):
            size+=len(chunk)
            if size>expected['bytes']:_fail('PACKAGE_CORRUPT','Payload grew after verification')
            digest.update(chunk);out.write(chunk)
    if size!=expected['bytes'] or digest.hexdigest()!=expected['sha256']:
        _fail('PACKAGE_CORRUPT','Payload changed after verification')


def _assertion_sql(rt, sql, bindings, forbidden_columns):
    if forbidden_columns is None:
        if not isinstance(sql,str):raise ValueError('SQL required')
        return sql
    if sql is not None or not isinstance(bindings,dict) or len(bindings)!=1:
        raise ValueError('forbidden_columns needs exactly one input binding and no SQL')
    if (not isinstance(forbidden_columns,list) or not 1<=len(forbidden_columns)<=256
            or any(not isinstance(n,str) or not n or len(n.encode())>1024 for n in forbidden_columns)):
        raise ValueError('forbidden_columns needs 1..256 bounded exact column names')
    metadata=_metadata(rt,next(iter(bindings.values())))
    present={f['name'] for f in metadata['fields']}
    found=sorted(present.intersection(forbidden_columns))
    if not found:return "SELECT CAST(NULL AS VARCHAR) column_name WHERE FALSE"
    literals=','.join("('"+name.replace("'","''")+"')" for name in found)
    return "SELECT column_name FROM (VALUES "+literals+") t(column_name)"
