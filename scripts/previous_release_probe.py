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
        '--old-schema','7','--new-schema','9','--report',str(ROOT/'benchmarks/local/upgrade-alpha8.json')],check=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/numeric_upgrade_probe.py'),str(old),str(ROOT/'target/release'),
        '--report',str(ROOT/'benchmarks/local/numeric-upgrade-alpha8.json')],check=True)
    print(json.dumps({'published_alpha8_archive_sha256':digest,'target':target,'status':'passed'}))

# Retain the schema-8 upgrade and verify schema-9 beta.2 interoperability too.
for previous,schema,hashes in [
    ('beta.1','8',{('Darwin','arm64'):'6dcdb07b5b505724669eb8b7a10dac0db2747e6f9a8fa2c708c1d6e55cad28b5',
                   ('Linux','x86_64'):'34f759a17a8d28165afbc8dfeeb5bfa7a1d858bb4704fd11f4e5905872faf351'}),
    ('beta.2','9',{('Darwin','arm64'):'c2620fd59aff6eac1628a71e32d87de56fd8791bcf787a3440633fc738c15e91',
                   ('Linux','x86_64'):'41c8d8e98a956d2ae209ade339a7de04ee4f08b2e19eaac5dae1f1dd6e65a266'})]:
    beta_hash=hashes[platform.system(),platform.machine()]
    name=f'rowtrail-0.1.0-{previous}-{target}'
    with tempfile.TemporaryDirectory(prefix='rowtrail-beta-upgrade-') as td:
        directory=pathlib.Path(td)
        subprocess.run(['gh','release','download',f'v0.1.0-{previous}','--repo','adam2go/rowtrail','--pattern',name+'.tar.xz','--dir',str(directory)],check=True)
        archive=directory/(name+'.tar.xz')
        assert hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()==beta_hash
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                assert member.name==name or member.name.startswith(name+'/')
                assert '..' not in pathlib.Path(member.name).parts and (member.isfile() or member.isdir())
            tar.extractall(directory,filter='data')
        subprocess.run([sys.executable,str(ROOT/'scripts/upgrade_probe.py'),str(directory/name),str(ROOT/'target/release'),
            '--old-schema',schema,'--new-schema','9','--report',str(ROOT/'benchmarks/local'/('upgrade-'+previous.replace('.','')+'.json'))],check=True)
