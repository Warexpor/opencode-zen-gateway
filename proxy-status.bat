@echo off
title OpenCode Zen gateway — SOCKS5 status
cd /d "%~dp0"

echo.
if exist "%~dp0socks5.url" (
  echo  SOCKS5 ON
  type "%~dp0socks5.url"
) else (
  echo  SOCKS5 OFF
  echo  Upstream goes direct to opencode.ai
)
echo.
pause
