"""Generate a third-party license inventory and preserve upstream notices."""
import json, os, pathlib, shutil, subprocess

root=pathlib.Path(__file__).resolve().parents[1]
cargo=os.environ.get('ROWTRAIL_CARGO') or shutil.which('cargo') or str(pathlib.Path.home()/'.cargo/bin/cargo')
metadata=json.loads(subprocess.check_output([cargo,'metadata','--locked','--format-version','1'],cwd=root))
directory=root/'third-party/licenses'
directory.mkdir(parents=True,exist_ok=True)
inventory=[]
for package in sorted(metadata['packages'],key=lambda p:(p['name'],p['version'])):
    if package['source'] is None:continue
    base=pathlib.Path(package['manifest_path']).parent
    folder=directory/(package['name']+'-'+package['version'])
    candidates=[p for p in base.iterdir() if p.is_file() and p.name.upper().startswith(('LICENSE','LICENCE','COPYING','NOTICE','COPYRIGHT','UNLICENSE','AUTHORS'))]
    if package['license_file']:
        explicit=base/package['license_file']
        if explicit.is_file() and explicit not in candidates:candidates.append(explicit)
    if candidates:
        folder.mkdir(exist_ok=True)
        for source in candidates:shutil.copyfile(source,folder/source.name)
    notices=sorted(str(p.relative_to(root)) for p in folder.iterdir() if p.is_file()) if folder.exists() else []
    inventory.append({'name':package['name'],'version':package['version'],'license':package['license'],'repository':package['repository'],'notice_files':notices})
(root/'third-party/inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
missing=[p['name'] for p in inventory if not p['license'] and not p['notice_files']]
assert not missing,missing
assert all(p['notice_files'] for p in inventory), 'Missing upstream notice files'
print(json.dumps({'dependencies':len(inventory),'without_license_declaration':missing}))
