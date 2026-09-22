"""Use an actual old package to verify safe handling of its legacy numeric data."""
import argparse,hashlib,json,os,pathlib,signal,subprocess,sys,tempfile,time
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'examples'))
from session_client import RowTrail,RowTrailError
p=argparse.ArgumentParser(description=__doc__);p.add_argument('old_bin_dir');p.add_argument('new_bin_dir')
p.add_argument('--report',default='benchmarks/local/numeric-upgrade.json');a=p.parse_args()
old=pathlib.Path(a.old_bin_dir).resolve();new=pathlib.Path(a.new_bin_dir).resolve();pids=[]
with tempfile.TemporaryDirectory(prefix='rowtrail-numeric-upgrade-') as td:
    ws=pathlib.Path(td)/'workspace';checks=[]
    try:
        with RowTrail(str(old/'rowtrail'),str(ws)) as rt:
            valid=rt.query('SELECT 9007199254740993::BIGINT n',label='valid old result')
            overflow=rt.query("SELECT SUM(n) FROM (VALUES ('9223372036854775807'::BIGINT),(1::BIGINT)) t(n)")
            assert rt.rows(overflow)==[['-9223372036854775808']]
            decimal=rt.query("SELECT SUM(n) FROM (VALUES ('"+'9'*38+"'::DECIMAL(38,0)),(1::DECIMAL(38,0))) t(n)")
            legacy_display=rt.rows(decimal)
            pid=rt.call('doctor',{})['coordinator_pid'];pids.append(pid)
        os.kill(pid,signal.SIGTERM)
        until=time.monotonic()+5
        while True:
            state=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
            if not state or state.startswith('Z'):break
            assert time.monotonic()<until;time.sleep(.01)
        pids.remove(pid)
        with RowTrail(str(new/'rowtrail'),str(ws)) as rt:
            pid=rt.call('doctor',{})['coordinator_pid'];pids.append(pid)
            assert rt.call('read',rt.binding(valid))['rows']==[['9007199254740993']]
            wrapped=rt.call('read',rt.binding(overflow))
            assert 'numeric' not in wrapped['quality']
            assert wrapped['rows']==[['-9223372036854775808']]
            checks.append('valid old values and immutable wrapped integers remain unchanged; legacy quality is unknown')
            for method in ('read','query','export'):
                target=pathlib.Path(td)/f'legacy.{method}.parquet'
                try:
                    if method=='read':rt.call('read',rt.binding(decimal))
                    elif method=='query':rt.query('SELECT * FROM t',{'t':decimal})
                    else:rt.export(decimal,target)
                    raise AssertionError('invalid old Decimal was accepted: '+method)
                except RowTrailError as e:
                    assert e.code=='ARITHMETIC_OVERFLOW',(method,e,e.response)
                    assert e.details['operation']=='decimal_output'
                    assert not target.exists()
            checks.append('invalid old Decimal fails read, derived SQL and export before display or final publication')
            result=rt.query('SELECT n+1 FROM t',{'t':valid})
            assert rt.rows(result)==[['9007199254740994']]
            assert result['observation']['quality']['numeric']['input_provenance']=='unknown'
            checks.append('new work on valid legacy data carries unknown input provenance')
        report={'status':'passed','checks':checks,'old_decimal_display':legacy_display,
            'old_version':subprocess.check_output([str(old/'rowtrail'),'--version'],text=True).strip(),
            'new_version':subprocess.check_output([str(new/'rowtrail'),'--version'],text=True).strip(),
            'binary_sha256':{name:{n:hashlib.file_digest((bins/n).open('rb'),'sha256').hexdigest() for n in ('rowtrail','rowtrail-runtime')} for name,bins in [('old',old),('new',new)]}}
        out=pathlib.Path(a.report);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
    finally:
        for pid in pids:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
