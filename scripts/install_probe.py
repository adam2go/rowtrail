"""Install the real archive in a private directory; reject a corrupt checksum."""
import json, os, pathlib, signal, subprocess, tempfile, tomllib
root=pathlib.Path(__file__).resolve().parents[1]
version=tomllib.loads((root/'Cargo.toml').read_text())['workspace']['package']['version']
archive=next((root/'dist').glob(f'rowtrail-{version}-*.tar.xz'))
with tempfile.TemporaryDirectory(prefix='rowtrail-install-test-') as directory:
    base=pathlib.Path(directory)
    env={**os.environ,'ROWTRAIL_INSTALL_DIR':str(base/'bin'),'ROWTRAIL_VERSION':version}
    subprocess.run(['sh',str(root/'install.sh'),'--from',str(archive)],env=env,check=True)
    binary=base/'bin/rowtrail'
    assert version in subprocess.check_output([str(binary),'--version'],text=True)
    workspace=base/'workspace'
    coordinator=None
    try:
        result=json.loads(subprocess.check_output([str(binary),'--workspace',str(workspace),'query','--sql','SELECT 42 answer','--wait-ms','1000']))['result']
        assert result['job']['state']=='completed' and result['observation']['rows']==[['42']],result
        coordinator=json.loads(subprocess.check_output([str(binary),'--workspace',str(workspace),'doctor']))['result']['coordinator_pid']
    finally:
        if coordinator:os.kill(coordinator,signal.SIGTERM)
    bad=base/archive.name
    bad.write_bytes(b'corrupt archive')
    bad.with_suffix('').with_suffix('.sha256').write_text('0'*64+'  '+bad.name+'\n')
    env['ROWTRAIL_INSTALL_DIR']=str(base/'rejected')
    attempt=subprocess.run(['sh',str(root/'install.sh'),'--from',str(bad)],env=env,capture_output=True,text=True)
    assert attempt.returncode!=0 and 'SHA-256 mismatch' in attempt.stderr,attempt
    assert not (base/'rejected').exists()
    print(json.dumps({'checksum_verified_install':True,'installed_symlink_query':True,'corrupt_archive_rejected':True}))
