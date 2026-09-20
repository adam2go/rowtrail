"""Package the two release executables with licenses and SHA-256 checksums."""
import hashlib
import json
import pathlib
import platform
import tarfile
import tomllib

root = pathlib.Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "Cargo.toml").read_text())["workspace"]["package"]["version"]
system = {"Darwin": "apple-darwin", "Linux": "unknown-linux-gnu"}[platform.system()]
arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64"}[platform.machine()]
name = f"rowtrail-{version}-{arch}-{system}"
dist = root / "dist"
dist.mkdir(exist_ok=True)
archive = dist / f"{name}.tar.xz"
sizes = {}
def archive_metadata(member):
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    return member

with tarfile.open(archive, "w:xz", preset=6) as output:
    for binary in ("rowtrail", "rowtrail-runtime"):
        source = root / "target/release" / binary
        sizes[binary] = source.stat().st_size
        output.add(source, arcname=f"{name}/{binary}", filter=archive_metadata)
    for item in ("README.md", "README.zh-CN.md", "LICENSE", "NOTICE", "third-party", "docs/progress.md"):
        output.add(root / item, arcname=f"{name}/{item}", filter=archive_metadata)
limits = json.loads((root / "benchmarks/budgets.json").read_text())["distribution"]
for binary,size in sizes.items():
    assert size <= limits[binary], (binary,size,limits[binary])
assert archive.stat().st_size <= limits["tar_xz"], (archive.stat().st_size,limits["tar_xz"])
digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
(dist / f"{name}.sha256").write_text(f"{digest}  {archive.name}\n")
metadata = {"version": version, "platform": platform.platform(), "artifact": archive.name,
            "sha256": digest, "compressed_bytes": archive.stat().st_size, "binary_bytes": sizes, "compression": "xz level 6", "size_budget_passed": True}
(dist / f"{name}.json").write_text(json.dumps(metadata, indent=2) + "\n")
print(json.dumps(metadata, indent=2))
