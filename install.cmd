@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "BIN=%USERPROFILE%\.local\bin"
if not exist "%BIN%" mkdir "%BIN%"

> "%BIN%\zen-gateway.cmd" (
  echo @echo off
  echo call "%~dp0zen-gateway.cmd" %%*
)

echo.
echo   Installed: %BIN%\zen-gateway.cmd
echo   Points at: %~dp0
echo.

echo %PATH% | find /I "%BIN%" >nul
if errorlevel 1 (
  echo   %BIN% is not on PATH.
  echo   Adding it for this user permanently...
  for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "OLD=%%B"
  if defined OLD (
    setx Path "!OLD!;%BIN%" >nul
  ) else (
    setx Path "%BIN%" >nul
  )
  echo   Open a new terminal, then run:  zen-gateway
) else (
  echo   Then run:  zen-gateway
)
echo   Health:    http://127.0.0.1:8789/healthz
echo.
pause
