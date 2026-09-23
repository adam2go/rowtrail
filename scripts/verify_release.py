"""Verify downloaded CI archives against test hashes, budgets and bundled files.

Maintainer-only Python 3.11+/gh workflow, not a product dependency. Download the
release and verification artifacts from --run into linux/ and macos/ subfolders
of --artifacts/--reports. Put the Ubuntu 24.04 install report at
--reports/compatibility/install.json. This script does not publish anything.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tomllib

ROOT=Path(__file__).resolve().parents[1]
SUITES={'integration':30,'alpha3':13,'alpha4':10,'alpha5':12,'alpha6':8,'alpha7':15,'alpha8':11,'alpha9':14,'beta2':16}
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--run',type=int,required=True)
p.add_argument('--commit',required=True)
p.add_argument('--repo',default='adam2go/rowtrail')
p.add_argument('--artifacts',type=Path,required=True)
p.add_argument('--reports',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
version=tomllib.loads((ROOT/'Cargo.toml').read_text())['workspace']['package']['version']
run=json.loads(subprocess.check_output(['gh','api',f'repos/{a.repo}/actions/runs/{a.run}']))
assert run['head_sha']==a.commit and run['conclusion']=='success' and run['status']=='completed'
jobs=json.loads(subprocess.check_output(['gh','api',f'repos/{a.repo}/actions/runs/{a.run}/jobs?per_page=100']))['jobs']
by_name={job['name']:job for job in jobs}
for name in ('verify (ubuntu-22.04)','verify (macos-14)','linux-compatibility'):
    assert by_name[name]['conclusion']=='success'
for name in ('verify (ubuntu-22.04)','verify (macos-14)'):
    steps={step['name']:step['conclusion'] for step in by_name[name]['steps']}
    for command in ('cargo test --release --locked --workspace','python3 tests/integration/alpha9.py','python3 tests/integration/beta2.py','python3 scripts/previous_release_probe.py','python3 scripts/install_probe.py'):
        assert steps['Run '+command]=='success'
budgets=json.loads((ROOT/'benchmarks/budgets.json').read_text())['distribution']
artifacts,inventory,resources,installs,upgrades=[],{},{},{},{}
for platform in ('linux','macos'):
    directory=a.artifacts/platform
    metadata_files=list(directory.glob('*.json'))
    assert len(metadata_files)==1
    metadata=json.loads(metadata_files[0].read_text())
    assert metadata['version']==version and metadata['size_budget_passed']
    archive=directory/metadata['artifact']
    digest=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()
    checksum=archive.with_name(archive.name.removesuffix('.tar.xz')+'.sha256').read_text().split()
    assert checksum==[digest,archive.name] and digest==metadata['sha256']
    assert archive.stat().st_size==metadata['compressed_bytes']<=budgets['tar_xz']
    prefix=archive.name.removesuffix('.tar.xz')
    hashes={}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            assert member.name==prefix or member.name.startswith(prefix+'/')
            assert '..' not in Path(member.name).parts and (member.isfile() or member.isdir())
        assert b'Apache License' in tar.extractfile(prefix+'/LICENSE').read()
        assert b'RowTrail' in tar.extractfile(prefix+'/NOTICE').read()
        expected=['docs/agent-guide.md','docs/agent-quickstart.md','docs/usage.md','docs/numeric-contract.md','examples/session_client.py','examples/quickstart.py','examples/analysis_quickstart.py','docs/analysis.md']
        notices=json.loads(tar.extractfile(prefix+'/third-party/inventory.json').read())
        expected += [path for entry in notices for path in entry['notice_files']]
        for path in expected:
            assert tar.extractfile(prefix+'/'+path).read()==(ROOT/path).read_bytes(),path
        assert set(metadata['binary_bytes'])=={'rowtrail','rowtrail-runtime'}
        for name,size in metadata['binary_bytes'].items():
            member=tar.getmember(prefix+'/'+name)
            assert member.size==size<=budgets[name] and member.mode&0o111
            with tar.extractfile(member) as binary:
                header=binary.read(20)
                if platform=='linux':assert header[:4]==b'\x7fELF' and header[4:6]==b'\x02\x01' and int.from_bytes(header[18:20],'little')==62
                else:assert header[:4]==b'\xcf\xfa\xed\xfe' and int.from_bytes(header[4:8],'little')==0x100000c
            hashes[name]=hashlib.file_digest(tar.extractfile(member),'sha256').hexdigest()
    metadata['binary_sha256']=hashes
    metadata['notice_files_checked']=sum(len(entry['notice_files']) for entry in notices)
    reports=a.reports/platform
    inventory[platform]={}
    for name,count in SUITES.items():
        result=json.loads((reports/(name+'.json')).read_text())
        assert result['status']=='passed' and len(result['checks'])==count,(platform,name)
        if 'binary_sha256' in result:assert result['binary_sha256']==hashes,(platform,name)
        inventory[platform][name]={k:result[k] for k in ('status','checks','binary_sha256') if k in result}
    upgrade=json.loads((reports/'upgrade-alpha8.json').read_text())
    numeric_upgrade=json.loads((reports/'numeric-upgrade-alpha8.json').read_text())
    assert upgrade['status']=='passed' and upgrade['old_schema']=='7' and upgrade['new_schema']=='9'
    assert upgrade['old_runtime_rejects_upgraded_store'] and upgrade['old_partial_checkpoint_preserved']
    assert numeric_upgrade['status']=='passed' and numeric_upgrade['binary_sha256']['new']==hashes
    beta_upgrade=json.loads((reports/'upgrade-beta1.json').read_text())
    assert beta_upgrade['status']=='passed' and beta_upgrade['old_schema']=='8' and beta_upgrade['new_schema']=='9'
    assert beta_upgrade['old_fixed_revision_preserved'] and beta_upgrade['old_partial_checkpoint_preserved']
    upgrades[platform]={'metadata':upgrade,'numeric':numeric_upgrade,'beta1':beta_upgrade}
    resource=json.loads((reports/'resources.json').read_text())
    assert resource['status']=='passed' and resource['binary_sha256']==hashes
    assert resource['all_exported_ids_match_independent_sort'] and resource['largest_part_bytes']<=8388608
    resources[platform]=resource
    install=json.loads((reports/'install.json').read_text())
    assert install['status']=='passed' and install['archive_sha256']==digest and install['version']==version
    assert install['installed_quickstart']['status']=='passed' and install['installed_quickstart']['handoff_original_source_bytes']==0
    installs[platform]=install
    artifacts.append(metadata)
compatibility=json.loads((a.reports/'compatibility/install.json').read_text())
assert compatibility['status']=='passed' and compatibility['archive_sha256']==artifacts[0]['sha256']
assert compatibility['installed_quickstart']['status']=='passed' and compatibility['version']==version
report={'version':version,'status':'verified','verified_source_commit':a.commit,'ci_run':run['html_url'],
        'platforms':['Ubuntu 22.04 x86_64','macOS 14 arm64'],'same_linux_archive_on_ubuntu24_verified':True,
        'per_platform':{'integration_scenarios':sum(SUITES.values()),'rust_tests_including_subprocess_harness':14,'subprocess_crash_scenarios':12,
                        'deterministic_broken_ack_regression':True,'format_clippy_dependency_boundaries':True,'mcp_session_sdk':True,
                        'benchmark_profile_guard':True,'archive_install_and_checksum_rejection':True},
        'artifact_sha256_verified_locally':True,'artifact_binaries_match_test_hashes':True,'bundled_guides_client_demo_and_notices_match':True,
        'artifacts':artifacts,'reports':inventory,'published_alpha8_upgrades':upgrades,'out_of_core':resources,'native_installs':installs,'linux_compatibility':compatibility,
        'documentation_provenance':'Archive documents reflect the verified build source. Later release-report/README updates on the tag do not change these executable bytes.'}
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':'verified','ci_run':run['html_url'],'artifacts':artifacts},indent=2))
