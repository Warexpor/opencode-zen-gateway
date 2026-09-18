@echo off
cd /d "%~dp0"
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo ERROR: Python 3 not found.
  pause
  exit /b 1
)
%PY% "%~dp0pack.py"
echo.
pause
