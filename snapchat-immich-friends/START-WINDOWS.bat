@echo off
setlocal EnableExtensions
title Snapchat Memories importer
cd /d "%~dp0"

rem ---- Find a real Python 3.9+ (the Microsoft Store "python" alias is only a stub, so test it)
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo Python 3.9 or newer is required.
  echo Install it from https://www.python.org/downloads/windows/
  echo During setup, tick "Add python.exe to PATH" and keep "tcl/tk and IDLE" selected.
  echo.
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)

rem ---- Private environment for this tool (re-created if missing or broken)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1 || rmdir /s /q ".venv"
)
if not exist ".venv\Scripts\python.exe" (
  echo Setting up for first use, this takes a minute...
  %PY% -m venv .venv
  if errorlevel 1 ( echo Could not create the Python environment. & pause & exit /b 1 )
)
".venv\Scripts\python.exe" -c "import requests, requests_toolbelt, piexif" >nul 2>&1
if errorlevel 1 (
  echo Installing required packages...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 ( echo Could not install required packages. Check your internet connection. & pause & exit /b 1 )
)

if defined SNAPIMPORT_SETUP_ONLY ( echo Setup OK & exit /b 0 )

rem ---- Open the window; fall back to console mode if Tk is not installed
".venv\Scripts\python.exe" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo Tk is not installed with this Python, so the console version will run instead.
  echo To get the window: re-run the Python installer, choose Modify, tick "tcl/tk and IDLE".
  ".venv\Scripts\python.exe" importer.py --cli
  exit /b
)
start "" ".venv\Scripts\pythonw.exe" importer.py
exit /b 0
