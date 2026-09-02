#!/bin/bash
set -u
cd "$(dirname "$0")"

PYTHON_CMD=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        PYTHON_CMD="$candidate"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    FOUND_VERSION="$(python3 --version 2>&1 || echo 'not installed')"
    osascript -e "display alert \"Newer Python required\" message \"Found: $FOUND_VERSION. Install Python 3.11 or newer from python.org/downloads/macos/, then run this launcher again.\"" 2>/dev/null || true
    echo "Python 3.11 or newer is required. Found: $FOUND_VERSION"
    echo "Download: https://www.python.org/downloads/macos/"
    exit 1
fi

if ! "$PYTHON_CMD" -c "import tkinter" >/dev/null 2>&1; then
    osascript -e 'display alert "Tkinter is unavailable" message "Install a current Python from python.org, then launch this tool again."' 2>/dev/null || true
    exit 1
fi

if [ -x ".venv/bin/python" ] && ! .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    BACKUP=".venv-incompatible-$(date +%Y%m%d-%H%M%S)"
    echo "Moving the environment created by an older Python to $BACKUP"
    mv .venv "$BACKUP" || exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "First-time setup: creating the local program environment..."
    "$PYTHON_CMD" -m venv .venv || exit 1
fi

if [ ! -f ".venv/.setup_complete" ]; then
    echo "Installing the image-tagging components..."
    .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt || exit 1
    touch .venv/.setup_complete
fi

.venv/bin/python px4_pix4d_gui.py
