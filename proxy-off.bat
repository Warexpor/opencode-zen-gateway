@echo off
title OpenCode Zen gateway — SOCKS5 OFF
cd /d "%~dp0"

if exist "%~dp0socks5.url" del /f /q "%~dp0socks5.url"

echo.
echo  SOCKS5 OFF
echo  Upstream goes direct to opencode.ai
echo.
echo  Restart START.cmd if the gateway is already running.
echo.
pause
