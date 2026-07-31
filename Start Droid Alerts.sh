#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if command -v python3 >/dev/null 2>&1; then
    BASE_PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    BASE_PYTHON="python"
else
    echo "Python 3.10 or newer is required."
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    "$BASE_PYTHON" -m venv .venv
fi

exec ".venv/bin/python" main.py gui
