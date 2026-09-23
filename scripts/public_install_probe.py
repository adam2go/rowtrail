"""After publication, test the public tag's default installer in a private folder.

Maintainer-only: checks the actual network download against verified native CI
hashes, the printable stdlib client, cross-TMPDIR handoff and the bundled demo.
It never changes a user's installation links or existing workspace.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import tomllib
import types

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--report', type=Path, required=True)
a = p.parse_args()
version = tomllib.loads((ROOT / 'Cargo.toml').read_text())['workspace']['package']['version']
verified = json.loads((ROOT / 'docs/release-verification.json').read_text())
assert verified['version'] == version and verified['status'] == 'verified'
target = {('Darwin', 'arm64'): 'aarch64-apple-darwin',
          ('Linux', 'x86_64'): 'x86_64-unknown-linux-gnu'}[platform.system(), platform.machine()]
artifact = next(v for v in verified['artifacts'] if target in v['artifact'])
url = f'https://raw.githubusercontent.com/adam2go/rowtrail/v{version}/install.sh'
pids = set()
previous = {key: os.environ.get(key) for key in ('TMPDIR', 'ROWTRAIL_RUNTIME')}
try:
    with tempfile.TemporaryDirectory(prefix='rowtrail-public-install-') as td:
        base = Path(td)
        installer = base / 'install.sh'
        subprocess.run(['curl', '-fsSL', url, '-o', str(installer)], check=True)
        assert installer.read_bytes() == (ROOT / 'install.sh').read_bytes()
        env = {**os.environ, 'ROWTRAIL_INSTALL_DIR': str(base / 'bin')}
        env.pop('ROWTRAIL_VERSION', None)
        env.pop('ROWTRAIL_RUNTIME', None)
        subprocess.run(['sh', str(installer)], env=env, check=True)
        binary = base / 'bin/rowtrail'
        package = binary.resolve().parent
        installed_version = subprocess.check_output([str(binary), '--version'], text=True).strip()
        assert installed_version == f'rowtrail-cli {version}'
        hashes = {n: hashlib.file_digest((package / n).open('rb'), 'sha256').hexdigest()
                  for n in ('rowtrail', 'rowtrail-runtime')}
        assert hashes == artifact['binary_sha256']
        printed = subprocess.check_output([str(binary), 'python-client'], text=True)
        assert printed == (package / 'examples/session_client.py').read_text()
        client = types.ModuleType('rowtrail_public_client')
        exec(compile(printed, 'rowtrail_public_client.py', 'exec'), client.__dict__)
        os.environ.pop('ROWTRAIL_RUNTIME', None)
        workspace = base / 'workspace'
        first_tmp, next_tmp = base / 'tmp-a', base / 'tmp-b'
        first_tmp.mkdir(); next_tmp.mkdir()
        os.environ['TMPDIR'] = str(first_tmp)
        with client.RowTrail(str(binary), str(workspace)) as rt:
            pid = rt.call('doctor', {})['coordinator_pid']; pids.add(pid)
            saved = rt.query('SELECT CAST(9007199254740993 AS BIGINT) id',
                             label='public beta handoff')
            assert rt.typed_rows(saved, numeric_policy='rowtrail-numeric-v1') == [[9007199254740993]]
        os.environ['TMPDIR'] = str(next_tmp)
        with client.RowTrail(str(binary), str(workspace)) as rt:
            assert rt.call('doctor', {})['coordinator_pid'] == pid
            found = rt.find('public beta handoff')
            assert rt.binding(found) == rt.binding(saved)
            answer = rt.query('SELECT SUM(CAST(id AS DECIMAL(38,0))) FROM t', {'t': found})
            assert rt.rows(answer) == [['9007199254740993']]
            assert answer['job']['metrics']['io']['source_read_bytes'] == 0
            try:
                rt.query('SELECT SUM(n) FROM (VALUES (9223372036854775807::BIGINT),(1::BIGINT)) t(n)')
                raise AssertionError('overflow was accepted')
            except client.RowTrailError as error:
                assert error.code == 'ARITHMETIC_OVERFLOW' and not error.retryable
        demo = json.loads(subprocess.check_output([
            sys.executable, str(package / 'examples/quickstart.py'), '--rowtrail', str(binary),
            '--directory', str(base / 'demo')], text=True))
        demo_pid = json.loads(subprocess.check_output([
            str(binary), '--workspace', demo['workspace'], 'doctor'], text=True))['result']['coordinator_pid']
        pids.add(demo_pid)
        assert demo['status'] == 'passed' and demo['handoff_original_source_bytes'] == 0
        analysis=json.loads(subprocess.check_output([
            sys.executable,str(package/'examples/analysis_quickstart.py'),'--rowtrail',str(binary),
            '--directory',str(base/'analysis')],text=True))
        assert analysis['status']=='passed' and analysis['total']==60 and analysis['modified_rows']==1
        for field in ('workspace','receiver'):
            analysis_pid=json.loads(subprocess.check_output([
                str(binary),'--workspace',analysis[field],'doctor'],text=True))['result']['coordinator_pid']
            pids.add(analysis_pid)
        agent=json.loads(subprocess.check_output([str(binary),'demo','--directory',str(base/'agent-demo'),'--rows','128'],text=True))
        assert agent['status']=='passed' and agent['checks']['separate_recipient_process'] and agent['checks']['original_csv_removed']
        for name in ('full','compact','receiver'):
            pids.add(json.loads(subprocess.check_output([str(binary),'--workspace',str(base/'agent-demo'/name),'doctor'],text=True))['result']['coordinator_pid'])
        report = {'agent_demo':{'status':agent['status'],**agent['checks']},'status': 'passed', 'installer_url': url,
                  'installer_sha256': hashlib.file_digest(installer.open('rb'), 'sha256').hexdigest(),
                  'version': installed_version, 'default_version_matches_release': True,
                  'public_network_download_and_checksum_install': True,
                  'installed_binary_sha256': hashes,
                  'binaries_match_verified_ci_archive': artifact['sha256'],
                  'printed_client_matches_bundle': True,
                  'analysis_snapshot_check_diff_package_import_recipe':{'status':analysis['status'],'total':analysis['total'],'modified_rows':analysis['modified_rows']},
                  'fresh_session_unique_label_handoff': True,
                  'changed_tmpdir_same_coordinator': True,
                  'lossless_large_integer': True, 'structured_sum_overflow': True,
                  'handoff_original_source_bytes': 0,
                  'bundled_demo': {k: v for k, v in demo.items() if k not in ('workspace', 'saved_binding')}}
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
finally:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
