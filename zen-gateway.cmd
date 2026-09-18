@echo off
rem Terminal entry on Windows. Double-click users can still use START.cmd.
cd /d "%~dp0"
call "%~dp0START.cmd" %*
