@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Andrew.ps1"
if errorlevel 1 (
    echo.
    echo Open Repair Andrew to recover a stuck service.
    pause
)
