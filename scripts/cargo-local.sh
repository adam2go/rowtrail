#!/bin/sh
set -eu
if [ "$(uname -s)" = Darwin ] && [ -d /Library/Developer/CommandLineTools ]; then
    export DEVELOPER_DIR=/Library/Developer/CommandLineTools
fi
exec "${ROWTRAIL_CARGO:-$HOME/.cargo/bin/cargo}" "$@"
