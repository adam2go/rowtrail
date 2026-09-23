"""Zero-download beta.2 demo: snapshot, check, diff, package, import, recipe."""
import argparse,json,pathlib,tempfile
from session_client import RowTrail
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--rowtrail',default='rowtrail');p.add_argument('--directory')
a=p.parse_args();base=pathlib.Path(a.directory or tempfile.mkdtemp(prefix='rowtrail-analysis-')).absolute()
base.mkdir(parents=True,exist_ok=True)
with RowTrail(a.rowtrail,str(base/'workspace')) as rt:
    # In an existing Python project, use df.to_parquet / df.write_parquet /
    # DuckDB COPY here instead. No dataframe library is required by RowTrail.
    original=rt.query("SELECT * FROM (VALUES (1,'Organic',10),(2,'organic',20),(3,'paid',30)) t(id,channel,amount)")
    rt.export(original,base/'clean.parquet')
    saved=rt.from_parquet(base/'clean.parquet',label='sessions',provenance={
        'origin':'analysis_quickstart.py','description':'Cleaned sessions before channel normalization',
        'code':'frame.to_parquet("clean.parquet", index=False)'})
    before=rt.query('SELECT * FROM t',{'t':saved})
    after=rt.query('SELECT id,lower(channel) channel,amount FROM t',{'t':saved},
        provenance={'description':'Normalize channel case'})
    check=rt.check('SELECT id FROM t GROUP BY id HAVING COUNT(*)>1',{'t':after},name='unique ids')
    assert check['passed']
    diff=rt.diff(before,after,keys=['id']);assert diff['rows']['modified']==1
    package=rt.pack(after,base/'handoff',include_inputs=True,notes='One channel spelling changed; amounts are unchanged.')
with RowTrail(a.rowtrail,str(base/'receiver')) as rt:
    imported=rt.import_package(base/'handoff')
    total=rt.scalar(rt.query('SELECT SUM(amount) FROM t',{'t':imported}));assert total==60
    recipe=imported['recipe']
    run=rt.run_recipe(recipe,{name:imported['mapping'][name] for name in recipe['inputs']},run_dir=base/'rerun')
    assert run['status']=='completed'
print(json.dumps({'status':'passed','workspace':str(base/'workspace'),'receiver':str(base/'receiver'),
    'package':str(base/'handoff'),'report':str(base/'handoff/report.md'),'run':str(base/'rerun/run.json'),
    'input_rows':3,'modified_rows':diff['rows']['modified'],'total':total,
    'package_nodes':len(package['nodes']),'sql_recomputed_only_on_explicit_run':True},indent=2))
