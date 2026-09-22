"""Inline durability, exact range reads, and bounded code composition."""
import argparse
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
from session_client import JobNotCompleted, RowTrail
from part_fixture import corrupt_part

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--bin-dir', default='target/release')
p.add_argument('--report', default='benchmarks/local/alpha7.json')
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


def rejects(function, message=None):
    try:
        function()
    except (ValueError, RuntimeError) as error:
        if message:
            assert message in str(error), error
        return error
    raise AssertionError('Expected explicit failure')


def stopped(pid):
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while True:
        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], text=True, capture_output=True).stdout.strip()
        if not state or state.startswith('Z'):
            return
        assert time.monotonic() < deadline
        time.sleep(.01)


with tempfile.TemporaryDirectory(prefix='rowtrail-alpha7-') as td:
    base = pathlib.Path(td)
    ws, pid, rt = base / 'workspace', None, None
    try:
        untouched = base / 'untouched'
        code = subprocess.check_output([str(bins / 'rowtrail'), '--workspace', str(untouched), 'python-client'], text=True)
        assert code == (ROOT / 'examples/session_client.py').read_text()
        compile(code, '<installed-client>', 'exec')
        assert not untouched.exists()
        check('installed CLI supplies the stdlib client without a runtime or workspace')

        subprocess.run([str(bins / 'rowtrail-runtime'), 'fixtures', '--directory', str(base / 'data'), '--rows', '65537'], check=True, stdout=subprocess.DEVNULL)
        rt = Logged(str(bins / 'rowtrail'), str(ws))
        pid = rt.call('doctor', {})['coordinator_pid']
        db = sqlite3.connect(ws / 'metadata.sqlite')
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '7'
        opened = rt.open(base / 'data/many.parquet')
        assert opened['binding'] == rt.binding(opened)
        literal = base / 'quarter [1] #?.parquet'
        os.link(base / 'data/many.parquet', literal)
        literal_source = rt.open(literal)
        assert rt.rows(rt.query('SELECT COUNT(*) FROM t', {'t': literal_source})) == [['65537']]
        check('frozen source paths with spaces and glob/URL characters remain literal file names')
        scalar = rt.query("SELECT CAST(9007199254740993 AS BIGINT) id, CAST(12.34 AS DECIMAL(20,2)) amount, CAST(NULL AS VARCHAR) n")
        assert rt.rows(scalar) == [['9007199254740993', '12.34', None]]
        fixed = rt.binding(scalar)
        assert scalar['binding'] == fixed and 'read' not in scalar['next_actions']
        part = db.execute('SELECT p.path,p.bytes,p.checksum,i.data FROM parts p JOIN inline_parts i USING(result_id,seq) WHERE p.result_id=?', [fixed['result_ref']]).fetchone()
        assert part and part[1] == len(part[3]) <= 131072
        assert hashlib.sha256(part[3]).hexdigest() == part[2] and not pathlib.Path(part[0]).exists()
        assert not (ws / 'store' / fixed['result_ref']).exists()
        check('small Arrow data is checksummed in SQLite with exact types and native fixed bindings')

        before = len(traces)
        branch = rt.query('SELECT id+1, amount FROM t', {'t': scalar})
        assert rt.rows(branch) == [['9007199254740994', '12.34']]
        assert [r['method'] for r in traces[before:]] == ['query']
        assert branch['job']['metrics']['io']['result_read_bytes'] == part[1]
        assert branch['job']['metrics']['io']['source_read_bytes'] == 0
        waited = rt.query('SELECT id FROM t', {'t': fixed}, execution={'wait_ms': 0})
        assert rt.rows(waited) == [['9007199254740993']]
        check('one code-level query composes responses without redundant read/schema calls and waits mechanically')

        for fmt in ('csv', 'parquet', 'arrow'):
            target = base / f'inline.{fmt}'
            export = rt.finish(rt.call('export', {**fixed, 'format': fmt, 'destination': str(target), 'execution': {'wait_ms': 1000}}))
            assert export['job']['state'] == 'completed', export
            assert target.exists() and export['job']['metrics']['io']['result_read_bytes'] == part[1]
        reopened = rt.open(base / 'inline.parquet')
        assert rt.rows(rt.query('SELECT * FROM t', {'t': reopened})) == rt.rows(scalar)
        check('inline results export through all three formats and reopen with exact values')

        exact = rt.query('SELECT CAST(7 AS BIGINT) n')
        size = exact['job']['metrics']['result_write_bytes']
        assert rt.rows(rt.query('SELECT CAST(7 AS BIGINT) n', execution={'result_bytes': size})) == [['7']]
        failure = rejects(lambda: rt.query('SELECT CAST(7 AS BIGINT) n', execution={'result_bytes': size - 1}))
        assert failure.response['job']['state'] == 'budget_exhausted'
        assert failure.response['readable_revision'] is None
        check('inline data obeys the encoded byte limit and cannot publish an incomplete final answer')

        # A first tiny UNION arm followed by a large arm uses both part stores.
        mixed = rt.query("SELECT CAST(42 AS BIGINT) id, 'small' AS payload UNION ALL SELECT id,payload FROM t", {'t': opened}, execution={'preview': 'available'})
        mixed_ref = rt.binding(mixed)
        stats = db.execute('SELECT COUNT(*),COUNT(i.data),SUM(p.bytes) FROM parts p LEFT JOIN inline_parts i USING(result_id,seq) WHERE p.result_id=?', [mixed_ref['result_ref']]).fetchone()
        assert stats[0] > stats[1] > 0, stats
        counted = rt.query('SELECT COUNT(*),MIN(id),MAX(id) FROM t', {'t': mixed})
        assert rt.rows(counted) == [['65538', '42', str(9007199254740993 + 65536)]]
        assert counted['job']['metrics']['io']['source_read_bytes'] == 0
        assert counted['job']['metrics']['io']['result_read_bytes'] >= stats[2]
        check('mixed inline/file results preserve all rows and source-free fixed-result reuse')

        assert stats[2] < 8 * 1024 * 1024
        rescanned = rt.query("SELECT 'all' k,COUNT(*) n FROM t UNION ALL SELECT 'big',COUNT(*) FROM t WHERE id>42 UNION ALL SELECT 'small',COUNT(*) FROM t WHERE id=42 ORDER BY k", {'t': mixed}, execution={'scan_bytes': stats[2]})
        assert rt.rows(rescanned) == [['all', '65538'], ['big', '65537'], ['small', '1']]
        io = rescanned['job']['metrics']['io']
        assert io['result_read_bytes'] == stats[2] and io['verified_cache_hits'] > 0
        assert io['verified_cache_peak_bytes'] <= 8 * 1024 * 1024
        check('three logical scans share verified parts inside one job under a one-pass byte budget')

        partial = rt.finish(rt.call('analyze', {'source': rt.binding(opened), 'aggregates': [{'function': 'count', 'alias': 'n'}], 'execution': {'wait_ms': 1000}}))
        derived = rt.query('SELECT * FROM t', {'t': {'result_ref': rt.binding(partial)['result_ref'], 'revision': 1}})
        assert derived['job']['state'] == 'completed'
        rejects(lambda: rt.rows(derived), 'partial')
        truncated = rt.query('SELECT id FROM t LIMIT 11', {'t': opened}, execution={'output': {'max_rows': 1, 'max_bytes': 8192}})
        rejects(lambda: rt.rows(truncated), 'truncated')
        assert rt.observe(derived)['observation']['quality']['coverage']['kind'] == 'partial'
        assert rt.observe(truncated)['observation']['next_cursor']
        check('guarded rows reject real partial-source and truncated observations without hiding their metadata')

        failure = rejects(lambda: rt.query('SELECT missing_column FROM t', {'t': opened}))
        assert isinstance(failure, JobNotCompleted) and failure.response['job']['error']['code'] == 'SQL_ERROR'
        rejects(lambda: rt.binding({'result_ref': fixed['result_ref'], 'revision': 0}))
        rejects(lambda: rt.binding({'result_ref': fixed['result_ref'], 'revision': True}))
        check('composition preserves failure responses and refuses unresolved or invalid fixed revisions')

        bad = RowTrail.__new__(RowTrail)
        bad.usable = True
        bad.process = subprocess.Popen([sys.executable, '-u', '-c', "import sys,json,time; sys.stdin.readline(); print(json.dumps({'api_version':'1','request_id':'wrong','ok':True,'result':{}}),flush=True); time.sleep(30)"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            try:
                bad.call('query', {'sql': 'SELECT 1', 'bindings': {}})
            except ConnectionError:
                pass
            else:
                raise AssertionError('Mismatched response was accepted')
            assert not bad.usable and bad.process.poll() is not None
            try:
                bad.call('query', {'sql': 'SELECT 1', 'bindings': {}})
            except ConnectionError:
                pass
            else:
                raise AssertionError('Broken transport was reused')
        finally:
            bad.__exit__()
        check('mismatched transport responses poison the client without replaying an ambiguous mutation')

        # Persist, close both processes, and rediscover a real durable result.
        rt.__exit__(); rt = None
        stopped(pid); pid = None
        rt = Logged(str(bins / 'rowtrail'), str(ws))
        pid = rt.call('doctor', {})['coordinator_pid']
        page = rt.call('workspace', {'action': 'summary', 'kind': 'result', 'limit': 100, 'max_bytes': 65536})
        found = next(item for item in page['items'] if item['ref'] == fixed['result_ref'])
        assert rt.rows(rt.query('SELECT id FROM t', {'t': found})) == [['9007199254740993']]
        check('coordinator restart and catalog handoff preserve inline result identity and values')

        # Corrupt after successful reuse: an old in-memory result must not mask it.
        corrupt_part(db, fixed['result_ref'])
        rejects(lambda: rt.call('read', fixed), 'RESULT_CORRUPT')
        bad = rt.finish(rt.call('query', {'bindings': {'t': fixed}, 'sql': 'SELECT COUNT(*) FROM t', 'execution': {'wait_ms': 1000}}))
        assert bad['job']['error']['code'] == 'RESULT_CORRUPT', bad
        target = base / 'corrupt.parquet'
        bad_export = rt.finish(rt.call('export', {**fixed, 'format': 'parquet', 'destination': str(target), 'execution': {'wait_ms': 1000}}))
        assert bad_export['job']['error']['code'] == 'RESULT_CORRUPT' and not target.exists()
        check('same-sized SQLite corruption fails read, saved SQL and export after earlier successful reuse')

        disposable = rt.query('SELECT CAST(99 AS BIGINT) n')
        ref = rt.binding(disposable)['result_ref']
        usage = rt.call('workspace', {'action': 'usage'})['stored_bytes']
        count = db.execute('SELECT COUNT(*) FROM inline_parts WHERE result_id=?', [ref]).fetchone()[0]
        assert count > 0
        rt.call('control', {'action': 'release', 'ref': ref})
        rt.call('workspace', {'action': 'gc', 'dry_run': False})
        assert db.execute('SELECT COUNT(*) FROM inline_parts WHERE result_id=?', [ref]).fetchone()[0] == 0
        assert rt.call('workspace', {'action': 'usage'})['stored_bytes'] < usage
        rejects(lambda: rt.call('read', rt.binding(disposable)), 'OBJECT_EXPIRED')
        check('GC expires references and reclaims quota-accounted inline bytes with their descriptors')

        low = rt.finish(rt.call('query', {'bindings': {'t': rt.binding(opened)}, 'sql': 'SELECT SUM(id),MAX(payload) FROM t', 'execution': {'wait_ms': 1000, 'scan_bytes': 4096}}))
        assert low['job']['state'] == 'budget_exhausted', low
        assert low['job']['metrics']['io']['source_read_bytes'] <= 4096
        assert low['job']['metrics']['io']['reserved_read_bytes'] <= 4096
        check('batched source ranges reserve scan bytes before I/O and retain hard scan limits')
        passed = True
    finally:
        if rt:
            rt.__exit__()
        if pid:
            try:
                stopped(pid)
            except ProcessLookupError:
                pass
        if 'db' in locals():
            db.close()
        def scrub(value):
            if isinstance(value, str):
                return value.replace(str(base), '<fixture>').replace(str(base).lstrip('/'), '<fixture>')
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, dict):
                return {k: scrub(v) for k, v in value.items()}
            return value
        report = ROOT / a.report
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({'status': 'passed' if passed else 'failed', 'binary_sha256': {name: hashlib.file_digest((bins / name).open('rb'), 'sha256').hexdigest() for name in ('rowtrail', 'rowtrail-runtime')}, 'checks': checks, 'traces': scrub(traces)}, ensure_ascii=False, indent=2) + '\n')
