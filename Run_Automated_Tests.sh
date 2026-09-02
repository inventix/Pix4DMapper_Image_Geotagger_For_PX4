#!/bin/sh
set -u
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11 or newer is required."
    exit 1
fi

[ -x ".test-venv/bin/python" ] || python3 -m venv .test-venv || exit 1
.test-venv/bin/python -m pip install --disable-pip-version-check -r requirements-dev.txt || exit 1
.test-venv/bin/python -m pytest -q
