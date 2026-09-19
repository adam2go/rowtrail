import os, pathlib, shutil, subprocess
root=pathlib.Path(__file__).resolve().parents[1]
cargo=os.environ.get('ROWTRAIL_CARGO') or shutil.which('cargo') or str(pathlib.Path.home()/'.cargo/bin/cargo')
for package in ('rowtrail-cli','rowtrail-client'):
    tree=subprocess.check_output([cargo,'tree','--locked','-p',package,'--edges','normal','--prefix','none'],cwd=root,text=True)
    forbidden=[line for line in tree.splitlines() if line.startswith(('datafusion','arrow','parquet','rusqlite','reqwest'))]
    assert not forbidden,(package,forbidden)
print('CLI and client have no engine, database, or HTTP client dependency')
