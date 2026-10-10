@echo off
rem MacroForge setup: finds Python 3.11+ and runs the guided setup. Safe to run again.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=python"
  )
)
if not defined PY (
  echo.
  echo MacroForge needs Python 3.11 or newer, and it was not found.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
  echo or run:  winget install Python.Python.3.12
  echo Then double-click setup.cmd again.
  echo.
  pause
  exit /b 1
)

%PY% macroforge.py setup %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
