@echo off
rem Local CI gate. Windows blocks .ps1 files by default, so this wrapper bypasses it for one run.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check.ps1"
exit /b %ERRORLEVEL%
