"""Interleaved durable materialization/reuse on incompressible integer columns.

Fixture generation and arithmetic oracle use Python's seeded PRNG. DuckDB only
converts the CSV fixture to Parquet before timing; it is not a product dependency.
"""
import argparse, csv, hashlib, json, os, pathlib, platform, random, signal
import sqlite3, statistics, subprocess, sys, tempfile, time
import duckdb
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'examples'))
from session_client import RowTrail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', action='append', required=True, help='name=bin-directory; repeat to compare builds')
    parser.add_argument('--rows', type=int, default=131072)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', default='benchmarks/local/reuse.json')
    args = parser.parse_args()
    variants = {name: (ROOT / directory).resolve() for name, directory in (v.split('=', 1) for v in args.variant)}
    records = []
    with tempfile.TemporaryDirectory(prefix='rowtrail-reuse-') as td:
        base = pathlib.Path(td); source = base / 'entropy.parquet'
        rng = random.Random(1761); sums = [0] * 8
        with (base / 'entropy.csv').open('w') as f:
            writer = csv.writer(f); writer.writerow(['id'] + [f'v{i}' for i in range(8)])
            for i in range(args.rows):
                values = [rng.getrandbits(63) for _ in sums]
                sums = [total + value for total, value in zip(sums, values)]
                writer.writerow([9007199254740993 + i] + values)
        con = duckdb.connect(); con.execute('SET threads=1')
        con.execute("COPY (SELECT * FROM read_csv($src, all_varchar=false)) TO $dst (FORMAT PARQUET, COMPRESSION UNCOMPRESSED, ROW_GROUP_SIZE 8192)", {'src': str(base / 'entropy.csv'), 'dst': str(source)})
        con.close()
        expected = [[str(args.rows)] + [str(n) for n in sums]]
        aggregate = 'SELECT COUNT(*), ' + ', '.join(f'SUM(CAST(v{i} AS DECIMAL(38,0)))' for i in range(8)) + ' FROM t'
        for repeat in range(args.repeats):
            names = list(variants)
            if repeat % 2: names.reverse()
            for name in names:
                bins = variants[name]; ws = base / f'{repeat}-{name}'; pid = None
                started = time.perf_counter(); stages = []
                try:
                    with RowTrail(str(bins / 'rowtrail'), str(ws)) as rt:
                        def query(sql, binding):
                            begin = time.perf_counter()
                            result = rt.call('query', {'bindings': {'t': binding}, 'sql': sql, 'execution': {'wait_ms': 1000, 'preview': 'none'}})
                            while result['job']['state'] in ('queued', 'running', 'stopping'):
                                result = rt.call('control', {'action': 'wait', 'ref': result['job']['id'], 'wait_ms': 1000})
                            assert result['job']['state'] == 'completed', result
                            stages.append({'elapsed_ms': (time.perf_counter() - begin) * 1000, 'metrics': result['job']['metrics']})
                            return result, {'result_ref': result['job']['result_ref'], 'revision': result['readable_revision']}
                        opened = rt.call('open', {'source': str(source)})
                        material, saved = query('SELECT * FROM t', {k: opened[k] for k in ('dataset_ref', 'manifest_ref')})
                        for _ in range(3):
                            result, ref = query(aggregate, saved)
                            page = result.get('observation') or rt.call('read', ref)
                            assert page['rows'] == expected, (page['rows'], expected)
                            assert result['job']['metrics']['io']['source_read_bytes'] == 0
                        elapsed = (time.perf_counter() - started) * 1000
                        pid = rt.call('doctor', {})['coordinator_pid']
                        with sqlite3.connect(ws / 'metadata.sqlite') as db:
                            sizes = [r[0] for r in db.execute('SELECT bytes FROM parts WHERE result_id=?', [saved['result_ref']])]
                        assert sizes and max(sizes) <= 8388608
                        records.append({'variant': name, 'repeat': repeat, 'total_ms': elapsed, 'stages': stages, 'parts': len(sizes), 'stored_bytes': sum(sizes), 'largest_part_bytes': max(sizes), 'independent_oracle_passed': True})
                finally:
                    if pid: os.kill(pid, signal.SIGTERM)
        def scrub(value):
            if isinstance(value, str): return value.replace(str(base), '<fixture>').replace(str(base).lstrip('/'), '<fixture>')
            if isinstance(value, list): return [scrub(v) for v in value]
            if isinstance(value, dict): return {k: scrub(v) for k, v in value.items()}
            return value
        summary = {name: statistics.median(r['total_ms'] for r in records if r['variant'] == name) for name in variants}
        report = {'kind': 'interleaved durable materialization plus three saved-result aggregates', 'platform': platform.platform(), 'rows': args.rows, 'repeats': args.repeats, 'fixture_sha256': hashlib.file_digest(source.open('rb'), 'sha256').hexdigest(), 'fixture_bytes': source.stat().st_size, 'variants': {name: {n: {'bytes': (bins / n).stat().st_size, 'sha256': hashlib.file_digest((bins / n).open('rb'), 'sha256').hexdigest()} for n in ('rowtrail', 'rowtrail-runtime')} for name, bins in variants.items()}, 'records': scrub(records), 'summary_ms': summary, 'limitations': ['OS cache is not flushed; fresh workspace and persistent session per trial.', 'Single machine, deterministic high-entropy numeric fixture; not an agent trial or a universal performance claim.', 'No RSS samples here; see the separate spill/resource workload.']}
        out = ROOT / args.output; out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(summary))

if __name__ == '__main__': main()
