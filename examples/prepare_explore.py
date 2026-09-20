"""Runnable alpha.3 workflow: profile, prepare, branch twice, export, release, GC.

Uses a tiny generated CSV and Python's standard library. The Parquet export and
quality sidecar remain after workspace data is explicitly released and collected.
"""
import argparse
import json
import pathlib
import tempfile
from session_client import RowTrail

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace', default='.rowtrail-example')
parser.add_argument('--rowtrail', default='rowtrail')
parser.add_argument('--output', default='rowtrail-example.parquet')
args = parser.parse_args()
results = []

with tempfile.TemporaryDirectory(prefix='rowtrail-example-') as directory, RowTrail(args.rowtrail, args.workspace) as client:
    call = client.call
    def finish(value):
        while value['job']['state'] in ('queued','running','stopping'):
            value = call('control', {'action':'wait','ref':value['job']['id'],'wait_ms':1000})
        if value['job']['state'] != 'completed':
            raise RuntimeError(value['job'])
        results.append(value['job']['result_ref'])
        return value
    def reference(value):
        return {'result_ref':value['job']['result_ref'],'revision':value['readable_revision']}
    def query(bindings, sql):
        return finish(call('query', {'bindings':bindings,'sql':sql,'execution':{'wait_ms':1000}}))
    source = pathlib.Path(directory)/'orders.csv'
    source.write_text('id,region,amount\n1,east,12.50\n2,east,8.25\n3,west,7.00\n4,west,\n')
    opened = call('open', {'source':str(source),'schema':[{'name':'id','type':'Int64'},{'name':'region','type':'Utf8'},{'name':'amount','type':'Decimal128(20, 2)'}]})
    profile = finish(call('inspect', {'ref':opened['manifest_ref'],'columns':['amount'],'checks':['null_count','min_max']}))
    prepared = call('prepare', {'source':{k:opened[k] for k in ('dataset_ref','manifest_ref')}})
    finish(prepared)
    binding = {k:prepared[k] for k in ('dataset_ref','manifest_ref')}
    saved = query({'orders':binding},'SELECT id,region,amount FROM orders WHERE amount IS NOT NULL')
    call('control', {'action':'pin','ref':reference(saved)['result_ref']})
    totals = query({'saved':reference(saved)},'SELECT region,SUM(amount) AS total FROM saved GROUP BY region ORDER BY region')
    positive = query({'saved':reference(saved)},'SELECT COUNT(*) AS positive_rows FROM saved WHERE amount>0')
    observations = {name:call('read',reference(value)) for name,value in [('profile',profile),('totals',totals),('positive',positive)]}
    exported = finish(call('export', {**reference(totals),'format':'parquet','destination':str(pathlib.Path(args.output).resolve())}))
    before = call('workspace', {'action':'usage'})
    for result in reversed(results):
        call('control', {'action':'release','ref':result})
    reclaimed = call('workspace', {'action':'gc','dry_run':False})
    print(json.dumps({'observations':observations,'branch_io':[totals['job']['metrics']['io'],positive['job']['metrics']['io']], 'export':exported['job']['metrics'],'storage_before':before,'storage_after_gc':reclaimed},ensure_ascii=False,indent=2))
