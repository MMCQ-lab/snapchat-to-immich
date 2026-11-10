@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Python 3 not found. Install it from https://www.python.org/downloads/windows/
    pause
    exit /b 1
  )
  set "PY=python"
)
%PY% -m venv .venv
if errorlevel 1 ( echo Could not create Python virtual environment & pause & exit /b 1 )
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 ( echo Could not install required Python packages & pause & exit /b 1 )
.venv\Scripts\python.exe importer.py
pause
