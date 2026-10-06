@echo off
rem Double-click me to start the Browser Agent dashboard.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dashboard.ps1"
if errorlevel 1 pause
