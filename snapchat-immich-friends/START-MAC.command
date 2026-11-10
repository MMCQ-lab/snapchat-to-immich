#!/bin/bash
# Double-click to start. First time: if macOS blocks it, see "macOS: first launch" in README.
cd "$(dirname "$0")" || exit 1

ok_python() {  # Python 3.9+ ?
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}
good_tk() {  # Tk 8.6+ ? (Apple's old built-in Tk 8.5 draws blank windows on modern macOS)
  "$1" -c 'import tkinter, sys; sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)' >/dev/null 2>&1
}

# Prefer python.org's installer (bundles a working Tk), then Homebrew, then anything on PATH.
PY=""
FALLBACK=""
for c in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
  [ -x "$c" ] && ok_python "$c" || continue
  [ -z "$FALLBACK" ] && FALLBACK="$c"
  if good_tk "$c"; then PY="$c"; break; fi
done
MODE=""
if [ -z "$PY" ]; then
  if [ -z "$FALLBACK" ]; then
    echo "Python 3.9 or newer is required. Opening python.org - install it, then double-click this file again."
    open "https://www.python.org/downloads/macos/"
    read -r -p "Press Enter to close..."
    exit 1
  fi
  PY="$FALLBACK"; MODE="--cli"
  echo "Note: this Python has no usable Tk, so the text version will run."
  echo "For the window version install Python from https://www.python.org/downloads/macos/"
  echo "(or with Homebrew: brew install python-tk)"
  echo
fi

# Private environment; rebuilt if the chosen Python changed.
if [ ! -x .venv/bin/python ] || [ "$(cat .venv/.source 2>/dev/null)" != "$PY" ]; then
  echo "Setting up for first use, this takes a minute..."
  rm -rf .venv
  "$PY" -m venv .venv || { read -r -p "Could not create environment. Press Enter..."; exit 1; }
  echo "$PY" > .venv/.source
fi
if ! .venv/bin/python -c 'import requests, requests_toolbelt, piexif' >/dev/null 2>&1; then
  echo "Installing required packages..."
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt \
    || { read -r -p "Could not install packages (internet?). Press Enter..."; exit 1; }
fi

[ -n "$SNAPIMPORT_SETUP_ONLY" ] && { echo "Setup OK ($PY) mode=${MODE:-window}"; exit 0; }
.venv/bin/python importer.py $MODE
