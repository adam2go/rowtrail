"""Fetch a pinned published alpha.8 archive and test the native beta upgrade.

Maintainer/CI only. Pin the archive digest independently of its downloaded
metadata; never install links or replace a user's workspace.
"""
import hashlib,json,pathlib,platform,subprocess,sys,tarfile,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
archives={
    ('Darwin','arm64'):('aarch64-apple-darwin','2c45b04b91922d3b1a81db20218190fdce780425224c3dc04fb0e3b7a2135d81'),
    ('Linux','x86_64'):('x86_64-unknown-linux-gnu','6209fd215fb1663cc31784f2315f4709086d38fcf80edab89078bbfbd3d25401')}
target,digest=archives[platform.system(),platform.machine()]
name=f'rowtrail-0.1.0-alpha.8-{target}'
with tempfile.TemporaryDirectory(prefix='rowtrail-published-upgrade-') as td:
    directory=pathlib.Path(td)
    subprocess.run(['gh','release','download','v0.1.0-alpha.8','--repo','adam2go/rowtrail','--pattern',name+'.tar.xz','--dir',str(directory)],check=True)
    archive=directory/(name+'.tar.xz')
    assert hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()==digest
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            assert member.name==name or member.name.startswith(name+'/')
            assert '..' not in pathlib.Path(member.name).parts and (member.isfile() or member.isdir())
        tar.extractall(directory,filter='data')
    old=directory/name
    assert '0.1.0-alpha.8' in subprocess.check_output([str(old/'rowtrail'),'--version'],text=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/upgrade_probe.py'),str(old),str(ROOT/'target/release'),
        '--old-schema','7','--new-schema','8','--report',str(ROOT/'benchmarks/local/upgrade-alpha8.json')],check=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/numeric_upgrade_probe.py'),str(old),str(ROOT/'target/release'),
        '--report',str(ROOT/'benchmarks/local/numeric-upgrade-alpha8.json')],check=True)
    print(json.dumps({'published_alpha8_archive_sha256':digest,'target':target,'status':'passed'}))
