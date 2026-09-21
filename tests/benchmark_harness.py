"""Guard profiling completeness without collecting an unbounded DuckDB result."""
import pathlib, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'benchmarks'))
from agent_pair import LoggedDuckDB

with tempfile.TemporaryDirectory() as td:
    base = pathlib.Path(td); con = LoggedDuckDB(base)
    con.execute('COPY (SELECT range id FROM range(10000)) TO ?', [str(base / 'input.parquet')])
    con.calls.clear()
    con.execute('SELECT SUM(id) FROM read_parquet(?)', [str(base / 'input.parquet')])
    assert con.fetchone() == (49995000,)
    assert con.calls[-1]['profile']['cumulative_rows_scanned'] == 10000
    con.execute('SELECT COUNT(*) FROM read_parquet(?)', [str(base / 'input.parquet')])
    assert con.fetchone() == (10000,)
    assert con.calls[-1]['profile']['cumulative_rows_scanned'] == 0
    con.execute('CREATE TEMP TABLE saved AS SELECT * FROM read_parquet(?)', [str(base / 'input.parquet')])
    assert con.calls[-1]['profile']['cumulative_rows_scanned'] == 10000
    con.execute('SELECT id FROM saved ORDER BY id')
    assert con.fetchone() == (0,)
    assert con.fetchmany(2) == [(1,), (2,)]
    assert con.fetchall() == [(i,) for i in range(3, 10000)]
    assert con.fetchone() is None
    assert con.calls[-1]['profile']['cumulative_rows_scanned'] == 10000
    con.execute('SHOW TABLES')
    assert con.fetchone() == ('saved',)
    assert all('profile' in call for call in con.profiles())
    con.close()
print('PASS scalar, metadata-only, materialization, lookahead ordering and catalog profiles')
