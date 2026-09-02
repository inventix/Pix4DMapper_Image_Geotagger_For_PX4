#!/bin/sh
set -u
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Install Python 3.11 or newer before building the Linux application."
    exit 1
fi

[ -x ".build-venv/bin/python" ] || python3 -m venv .build-venv || exit 1
.build-venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt pyinstaller || exit 1
.build-venv/bin/python -m PyInstaller \
  --noconfirm --clean --onefile --windowed \
  --name "PX4_Pix4D_Tagger" \
  --collect-all pyulog \
  px4_pix4d_gui.py || exit 1

cp course_config.json dist/course_config.json
echo "Build complete: $PWD/dist/PX4_Pix4D_Tagger"
echo "Build on the oldest Linux distribution that the resulting binary must support."
