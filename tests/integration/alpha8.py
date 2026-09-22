"""Bounded handoff discovery and resource-aware SQL parallelism."""
import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
import os
import pathlib
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'examples'))
from session_client import RowTrail

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--bin-dir', default='target/release')
p.add_argument('--report', default='benchmarks/local/alpha8.json')
a = p.parse_args()
bins = (ROOT / a.bin_dir).resolve()
checks, traces = [], []
passed = False

class Logged(RowTrail):
    def call(self, method, params, idempotency_key=None):
        value = super().call(method, params, idempotency_key)
        traces.append({'method': method, 'params': params, 'result': value})
        return value

def check(name):
    checks.append(name)
    print('PASS', name, flush=True)

def rejects(fn, code):
    try:
        fn()
    except RuntimeError as error:
        assert code in str(error), error
        return
    raise AssertionError('expected ' + code)

with tempfile.TemporaryDirectory(prefix='rowtrail-alpha8-') as td:
    base = pathlib.Path(td)
    ws, pid, rt = base / 'workspace', None, None
    try:
        n = 131073
        subprocess.run([str(bins/'rowtrail-runtime'), 'fixtures', '--directory', str(base/'data'), '--rows', str(n)], check=True, stdout=subprocess.DEVNULL)
        rt = Logged(str(bins/'rowtrail'), str(ws))
        pid = rt.call('doctor', {})['coordinator_pid']
        db = sqlite3.connect(ws/'metadata.sqlite')
        opened = rt.open(base/'data/many.parquet', label='orders 原始数据')
        small = rt.open(base/'data/small.csv', label='small')
        assert opened['label'] == 'orders 原始数据'
        page = rt.call('workspace', {'action':'summary', 'kind':'dataset', 'label':'orders 原始数据'})
        item, = page['items']
        assert item['row_count'] == n and item['schema_hint']['field_count'] == 5
        assert item['schema_hint']['omitted_fields'] == 1
        assert item['binding'] == opened['binding']
        assert rt.call('workspace', {'action':'summary', 'kind':'dataset', 'label':'small'})['items'][0]['row_count'] is None
        rt.call('control', {'action':'refresh', 'ref':small['dataset_ref']})
        assert rt.call('workspace', {'action':'summary', 'kind':'dataset', 'label':'small'})['items'][0]['label'] == 'small'
        check('dataset labels, bounded exact field hints, known/unknown row counts and refresh preservation')

        saved = rt.query('SELECT id,region,amount FROM t WHERE amount IS NOT NULL', {'t':opened}, label='non-null orders', execution={'output':{'max_rows':0}})
        expected_n = sum(i % 17 != 0 for i in range(n))
        fixed = rt.binding(saved)
        rt.__exit__()
        rt = Logged(str(bins/'rowtrail'), str(ws))
        before = len(traces)
        page = rt.call('workspace', {'action':'summary', 'kind':'result', 'label':'non-null orders'})
        item, = page['items']
        assert item['binding'] == fixed and item['row_count'] == expected_n
        assert [f['name'] for f in item['schema_hint']['fields']] == ['id','region','amount']
        recovered = rt.query('SELECT COUNT(*) n,MIN(id) first_id FROM s', {'s':item})
        assert rt.rows(recovered) == [[str(expected_n), '9007199254740994']]
        assert recovered['job']['metrics']['io']['source_read_bytes'] == 0
        assert [t['method'] for t in traces[before:]] == ['workspace','query']
        check('fresh session identifies and reuses a saved result in one catalog plus one query, without schema probes or source bytes')

        first = rt.query('SELECT 1 x', label='same')
        second = rt.query('SELECT 2 x', label='same')
        page = rt.call('workspace', {'action':'summary', 'kind':'result', 'label':'same', 'limit':1})
        assert page['next_cursor'] and page['items'][0]['binding'] == rt.binding(first)
        later = rt.query('SELECT 3 x', label='same')
        rejects(lambda:rt.call('workspace', {'action':'summary', 'kind':'result', 'label':'other', 'cursor':page['next_cursor']}), 'INVALID_CURSOR')
        page2 = rt.call('workspace', {'action':'summary', 'kind':'result', 'label':'same', 'limit':1, 'cursor':page['next_cursor']})
        assert page2['items'][0]['binding'] == rt.binding(second) and page2['next_cursor'] is None
        check('duplicate labels remain distinct fixed bindings; filtered cursor freezes membership and rejects filter changes')

        wide = rt.query('SELECT '+','.join(f'{i} AS "'+('宽'*90 if i == 0 else f'field_{i}')+'"' for i in range(40)), label='wide')
        page = rt.call('workspace', {'action':'summary', 'kind':'result', 'label':'wide', 'max_bytes':2048})
        hint = page['items'][0]['schema_hint']
        assert hint['field_count'] == 40 and hint['omitted_fields'] >= 36
        assert hint['fields'][0]['name'] == '宽'*90
        enormous = rt.query('SELECT 1 AS "'+('列'*2000)+'"', label='long field', execution={'output':{'max_rows':0, 'max_bytes':16384}})
        page = rt.call('workspace', {'action':'summary', 'kind':'result', 'label':'long field', 'max_bytes':2048})
        assert page['items'][0]['schema_hint'] == {'field_count':1, 'omitted_fields':1, 'fields':[]}
        assert page['items'][0]['binding'] == rt.binding(enormous)
        check('wide and long Unicode schemas preserve exact names or explicitly omit them within catalog byte budgets')

        count = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        for invalid in ['', '  ', 'x\ny', '界'*86]:
            rejects(lambda:rt.query('SELECT 1', label=invalid), 'INVALID_ARGUMENT')
            rejects(lambda:rt.open(base/'data/small.csv', label=invalid), 'INVALID_ARGUMENT')
            rejects(lambda:rt.call('workspace', {'action':'summary','label':invalid}), 'INVALID_ARGUMENT')
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == count
        one = rt.query('SELECT 42', label='once', idempotency_key='label-key')
        two = rt.query('SELECT 42', label='once', idempotency_key='label-key')
        assert rt.binding(one) == rt.binding(two)
        rejects(lambda:rt.query('SELECT 42', label='different', idempotency_key='label-key'), 'IDEMPOTENCY_CONFLICT')
        check('invalid labels fail before admission; idempotent labels keep their result and reject changed requests')

        rt.call('control', {'action':'release','ref':rt.binding(later)['result_ref']})
        rt.call('workspace', {'action':'gc','dry_run':False})
        page = rt.call('workspace', {'action':'summary','kind':'result','label':'same'})
        expired = next(x for x in page['items'] if x['ref'] == rt.binding(later)['result_ref'])
        assert expired['stored_validity'] == 'expired' and not expired['next_actions']
        check('labels and schema hints do not turn expired results into readable bindings')

        serial = rt.query('SELECT 9007199254740993 id', execution={'memory_bytes':8*1024*1024})
        assert serial['job']['metrics']['target_partitions'] == 1
        assert rt.rows(serial) == [['9007199254740993']]
        automatic = rt.query('SELECT COUNT(amount) n FROM t', {'t':opened})
        assert automatic['job']['metrics']['target_partitions'] in (1,2)
        assert rt.rows(automatic) == [[str(expected_n)]]
        check('small/8 MiB queries stay serial and large automatic plans report their bounded partition target')

        expected = defaultdict(lambda:[0,0])
        for i in range(n):
            if i % 17:
                key = None if i % 13 == 0 else ['华东','华南','华北'][i % 3]
                expected[key][0] += 1
                expected[key][1] += i % 2001 - 1000
        oracle = sorted([[key,str(v[0]),format(Decimal(v[1])/100,'.2f')] for key,v in expected.items()], key=str)
        for target in (1,2,4):
            execution = {'target_partitions':target, 'memory_bytes':max(128,target*64)*1024*1024}
            aggregate = rt.query('SELECT region,COUNT(*),SUM(amount) FROM t WHERE amount IS NOT NULL GROUP BY region', {'t':opened}, execution=execution)
            joined = rt.query('SELECT a.region,COUNT(*),SUM(a.amount) FROM t a JOIN t b ON a.id=b.id WHERE b.amount IS NOT NULL GROUP BY a.region', {'t':opened}, execution=execution)
            assert aggregate['job']['metrics']['target_partitions'] == joined['job']['metrics']['target_partitions'] == target
            assert sorted(rt.rows(aggregate),key=str) == sorted(rt.rows(joined),key=str) == oracle
        check('one/two/four partition aggregates and hash joins agree with independent integer/Decimal/null arithmetic')

        ordered = rt.query('SELECT id,payload FROM t ORDER BY payload,id DESC', {'t':opened}, execution={'target_partitions':2,'memory_bytes':128*1024*1024,'output':{'max_rows':0}})
        expected_ids = [str(9007199254740993+i) for i in sorted(range(n),key=lambda i:(i*6364136223846793005)&((1<<64)-1))]
        values, cursor = [], None
        while True:
            page = rt.call('read', {**rt.binding(ordered),'columns':['id'],'max_rows':10000,'max_bytes':1000000,**({'cursor':cursor} if cursor else {})})
            values += [row[0] for row in page['rows']]
            cursor = page['next_cursor']
            if not cursor: break
        assert values == expected_ids
        check('parallel wide sort preserves the independently computed ordering of every exported observation row')

        count = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        for target, memory in ((0,128),(9,1024),(2,32),(4,128)):
            rejects(lambda:rt.query('SELECT 1', execution={'target_partitions':target,'memory_bytes':memory*1024*1024}), 'INVALID_ARGUMENT')
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == count
        limited = rt.finish(rt.call('query', {'sql':'SELECT SUM(CAST(id AS DECIMAL(38,0))) FROM t','bindings':{'t':rt.binding(opened)},'execution':{'target_partitions':2,'scan_bytes':1024,'wait_ms':1000}}))
        assert limited['job']['state'] == 'budget_exhausted' and limited['job']['error']['code'] == 'RESOURCE_EXHAUSTED', limited
        assert limited['job']['metrics']['io']['reserved_read_bytes'] <= 1024
        assert limited['job']['metrics']['io']['source_read_bytes'] <= 1024
        check('unsafe partition/pool combinations fail before admission and concurrent scans obey the shared byte budget')

        active = rt.call('query', {'sql':'SELECT COUNT(*) FROM t a CROSS JOIN t b WHERE a.id + b.id > 0','bindings':{'t':rt.binding(opened)},'execution':{'target_partitions':2,'wait_ms':0}})
        deadline = time.monotonic()+10
        while active['job']['state'] == 'queued':
            assert time.monotonic() < deadline
            active = rt.call('control', {'action':'wait','ref':active['job']['id'],'wait_ms':1})
        rt.call('control', {'action':'cancel','ref':active['job']['id']})
        stopped = rt.finish(active)
        assert stopped['job']['state'] == 'cancelled' and stopped['job']['metrics']['worker_exit_confirmed'], stopped
        assert rt.rows(rt.query('SELECT 42')) == [['42']]
        check('parallel cancellation confirms worker exit; the next query starts cleanly without replay')
        passed = True
    finally:
        if rt: rt.__exit__()
        if pid:
            try: os.kill(pid, signal.SIGTERM)
            except ProcessLookupError: pass
        def scrub(v):
            if isinstance(v,str): return v.replace(str(base),'<fixture>').replace(str(base).lstrip('/'),'<fixture>')
            if isinstance(v,list): return [scrub(x) for x in v]
            if isinstance(v,dict): return {k:scrub(x) for k,x in v.items()}
            return v
        report = {'status':'passed' if passed else 'incomplete','checks':checks,'binary_sha256':{name:hashlib.file_digest((bins/name).open('rb'),'sha256').hexdigest() for name in ('rowtrail','rowtrail-runtime')},'traces':scrub(traces)}
        out = ROOT/a.report
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':report['status'],'checks':len(checks)}))
