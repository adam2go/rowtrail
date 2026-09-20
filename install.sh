#!/bin/sh
# Install a verified native release without a language runtime or package manager.
set -eu
version=${ROWTRAIL_VERSION:-0.1.0-alpha.3}
install_dir=${ROWTRAIL_INSTALL_DIR:-"$HOME/.local/bin"}
case "$version" in ''|*[!A-Za-z0-9.-]*) echo 'Invalid ROWTRAIL_VERSION' >&2; exit 1;; esac
case "$(uname -s):$(uname -m)" in
    Darwin:arm64) target=aarch64-apple-darwin ;;
    Linux:x86_64) target=x86_64-unknown-linux-gnu ;;
    *) echo 'Available release targets: macOS arm64 and Linux x86_64.' >&2; exit 1 ;;
esac
name="rowtrail-$version-$target"
archive="$name.tar.xz"
staging=$(mktemp -d "${TMPDIR:-/tmp}/rowtrail-install.XXXXXX")
trap 'rm -rf "$staging"' EXIT HUP INT TERM
if [ "${1:-}" = '--from' ] && [ "$#" -eq 2 ]; then
    cp "$2" "$staging/$archive"
    cp "${2%.tar.xz}.sha256" "$staging/$name.sha256"
elif [ "$#" -eq 0 ]; then
    base="https://github.com/adam2go/rowtrail/releases/download/v$version"
    curl --fail --location --retry 3 --proto '=https' "$base/$archive" -o "$staging/$archive"
    curl --fail --location --retry 3 --proto '=https' "$base/$name.sha256" -o "$staging/$name.sha256"
else
    echo 'Usage: sh install.sh [--from /path/to/rowtrail-VERSION-TARGET.tar.xz]' >&2
    exit 1
fi
expected=$(awk 'NF == 2 { print $1 }' "$staging/$name.sha256")
case "$expected" in ''|*[!0-9a-f]*) echo 'Invalid SHA-256 file' >&2; exit 1;; esac
[ "${#expected}" -eq 64 ] || { echo 'Invalid SHA-256 length' >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then
    actual=$(sha256sum "$staging/$archive" | awk '{print $1}')
else
    actual=$(shasum -a 256 "$staging/$archive" | awk '{print $1}')
fi
[ "$actual" = "$expected" ] || { echo 'SHA-256 mismatch; installation stopped.' >&2; exit 1; }
tar -xJf "$staging/$archive" -C "$staging"
[ -f "$staging/$name/rowtrail" ] && [ -f "$staging/$name/rowtrail-runtime" ]
mkdir -p "$install_dir"
install_dir=$(cd "$install_dir" && pwd -P)
# Retain notices beside immutable versioned binaries. Never overwrite a running
# executable. Each version's client and runtime stay in the same directory.
if [ -e "$install_dir/$name" ]; then
    echo "Version directory already exists: $install_dir/$name" >&2
    exit 1
fi
mv "$staging/$name" "$install_dir/$name"
chmod 755 "$install_dir/$name/rowtrail" "$install_dir/$name/rowtrail-runtime"
for executable in rowtrail rowtrail-runtime; do
    ln -s "$name/$executable" "$install_dir/.$executable-$$"
    mv -f "$install_dir/.$executable-$$" "$install_dir/$executable"
done
printf 'Installed RowTrail %s in %s\n' "$version" "$install_dir"
printf 'Add this directory to PATH if needed. Verify with: %s/rowtrail --version\n' "$install_dir"
