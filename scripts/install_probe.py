"""Install the real archive in a private directory; reject a corrupt checksum."""
import hashlib, io, json, os, pathlib, platform, signal, subprocess, tarfile, tempfile, tomllib
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
    package=binary.resolve().parent
    for name in ('docs/agent-guide.md','docs/agent-quickstart.md','docs/usage.md','examples/session_client.py','examples/quickstart.py'):
        assert (package/name).is_file(), name
    assert subprocess.check_output([str(binary),'python-client'],text=True)==(package/'examples/session_client.py').read_text()
    demo=json.loads(subprocess.check_output(['python3',str(package/'examples/quickstart.py'),'--rowtrail',str(binary),'--directory',str(base/'demo')],text=True))
    demo_pid=json.loads(subprocess.check_output([str(binary),'--workspace',demo['workspace'],'doctor']))['result']['coordinator_pid']
    os.kill(demo_pid,signal.SIGTERM)
    assert demo['status']=='passed' and demo['handoff_original_source_bytes']==0
    bad=base/archive.name
    bad.write_bytes(b'corrupt archive')
    bad.with_suffix('').with_suffix('.sha256').write_text('0'*64+'  '+bad.name+'\n')
    env['ROWTRAIL_INSTALL_DIR']=str(base/'rejected')
    attempt=subprocess.run(['sh',str(root/'install.sh'),'--from',str(bad)],env=env,capture_output=True,text=True)
    assert attempt.returncode!=0 and 'SHA-256 mismatch' in attempt.stderr,attempt
    assert not (base/'rejected').exists()
    original=binary.resolve()
    native_rejections=[]
    for broken in ('cli','runtime','version'):
        test_version='0.0.0-broken-'+broken
        test_archive=base/archive.name.replace(version,test_version)
        prefix=test_archive.name.removesuffix('.tar.xz')
        with tarfile.open(test_archive,'w:xz') as tar:
            for executable in ('rowtrail','rowtrail-runtime'):
                fails=(broken=='cli' and executable=='rowtrail') or (broken=='runtime' and executable=='rowtrail-runtime')
                reported='0.0.0-wrong' if broken=='version' else test_version
                script=('#!/bin/sh\necho synthetic-incompatible-build >&2\nexit 1\n' if fails else '#!/bin/sh\necho "rowtrail-cli '+reported+'"\n').encode()
                info=tarfile.TarInfo(prefix+'/'+executable);info.size=len(script);info.mode=0o755
                tar.addfile(info,io.BytesIO(script))
        digest=hashlib.file_digest(test_archive.open('rb'),'sha256').hexdigest()
        test_archive.with_name(prefix+'.sha256').write_text(digest+'  '+test_archive.name+'\n')
        attempt=subprocess.run(['sh',str(root/'install.sh'),'--from',str(test_archive)],env={**env,'ROWTRAIL_VERSION':test_version,'ROWTRAIL_INSTALL_DIR':str(base/'bin')},capture_output=True,text=True)
        assert attempt.returncode!=0 and 'existing installation is unchanged' in attempt.stderr,attempt
        assert binary.resolve()==original and not (base/'bin'/prefix).exists()
        native_rejections.append(broken)
    fake=base/'fake-tools';fake.mkdir()
    (fake/'uname').write_text('#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
    (fake/'curl').write_text('#!/bin/sh\necho CURL_CALLED >&2\nexit 99\n')
    for f in fake.iterdir():f.chmod(0o755)
    preflights=[]
    for libc,allowed in [('glibc 2.34',False),('glibc 2.35',True),('glibc 2.39',True),('musl 1.2.5',False),('',False)]:
        (fake/'getconf').write_text('#!/bin/sh\nprintf "%s\\n" "'+libc+'"\n')
        (fake/'getconf').chmod(0o755)
        attempt=subprocess.run(['sh',str(root/'install.sh')],env={**env,'PATH':str(fake)+os.pathsep+os.environ['PATH']},capture_output=True,text=True)
        assert ('CURL_CALLED' in attempt.stderr)==allowed,attempt
        if not allowed:assert 'requires glibc 2.35' in attempt.stderr
        preflights.append({'reported_libc':libc,'download_allowed':allowed})
    report={'status':'passed','platform':platform.platform(),'version':version,
            'archive_sha256':hashlib.file_digest(archive.open('rb'),'sha256').hexdigest(),
            'checksum_verified_install':True,'installed_symlink_query':True,'corrupt_archive_rejected':True,
            'bundled_docs_and_printed_client_match':True,'installed_quickstart':{k:v for k,v in demo.items() if k not in ('workspace','saved_binding')},
            'incompatible_packages_rejected_before_symlink_changes':native_rejections,
            'linux_libc_preflight':preflights}
    out=root/'benchmarks/local/install.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
