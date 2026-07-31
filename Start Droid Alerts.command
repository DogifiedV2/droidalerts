#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

if command -v python3 >/dev/null 2>&1; then
    BASE_PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    BASE_PYTHON="python"
else
    echo "Python 3.10 or newer is required."
    read -r -p "Press Return to close..."
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    "$BASE_PYTHON" -m venv .venv
    STATUS=$?
    if [ "$STATUS" -ne 0 ]; then
        echo
        read -r -p "Could not create the Python environment. Press Return to close..."
        exit "$STATUS"
    fi
fi

".venv/bin/python" main.py gui
STATUS=$?
if [ "$STATUS" -ne 0 ]; then
    echo
    read -r -p "Droid Alerts exited with an error. Press Return to close..."
fi
exit "$STATUS"
