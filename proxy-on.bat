@echo off
title OpenCode Zen gateway — SOCKS5 ON
cd /d "%~dp0"

set "URL=%~1"
if "%URL%"=="" set "URL=socks5://127.0.0.1:10808"

> "%~dp0socks5.url" echo %URL%

echo.
echo  SOCKS5 ON
echo  %URL%
echo.
echo  Only this gateway's calls to opencode.ai use the proxy.
echo  Restart START.cmd if the gateway is already running.
echo.
pause
