@echo off
setlocal EnableDelayedExpansion
title OpenCode Zen gateway (close to stop)
cd /d "%~dp0"

set "SOCKS_STATE=OFF"
set "SOCKS_URL="
if exist "%~dp0socks5.url" (
  set "SOCKS_STATE=ON"
  set /p SOCKS_URL=<"%~dp0socks5.url"
)

echo.
echo  ============================================
echo   OpenCode Zen gateway
echo   http://127.0.0.1:8789/v1
echo   Upstream: https://opencode.ai/zen
echo   SOCKS5: !SOCKS_STATE!
if not "!SOCKS_URL!"=="" echo   !SOCKS_URL!
echo   Optional: set OPENCODE_API_KEY
echo   Logs: %~dp0logs\
echo   Close this window = stop
echo  ============================================
echo.

rem Prefer `py -3` so a random venv `python` on PATH is not used.
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  python3 -c "import sys" >nul 2>&1 && set "PY=python3"
)
if not defined PY (
  echo ERROR: Python 3 not found. Install it or add py.exe to PATH.
  pause
  exit /b 1
)

if /I "!SOCKS_STATE!"=="ON" (
  %PY% -c "import socks" >nul 2>&1
  if errorlevel 1 (
    echo ERROR: SOCKS5 is ON but this Python has no PySocks:
    %PY% -c "import sys; print(' ', sys.executable)"
    echo   Install with:  %PY% -m pip install PySocks
    echo   Or run proxy-off.bat to go direct.
    echo.
    pause
    exit /b 1
  )
)

echo Using: %PY%
%PY% "%~dp0gateway.py"
set ERR=%ERRORLEVEL%
echo.
if not %ERR%==0 (
  echo Gateway exited with code %ERR%.
  pause
)
exit /b %ERR%
