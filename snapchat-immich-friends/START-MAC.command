#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3 required: https://www.python.org/downloads/macos/'
  read -r -p 'Press Enter to exit...'
  exit 1
fi
python3 -m venv .venv || exit 1
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt || exit 1
.venv/bin/python importer.py
