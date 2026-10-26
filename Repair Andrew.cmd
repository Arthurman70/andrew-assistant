@echo off
title Repair Andrew
echo Checking Andrew and restoring services. Your settings and memories will be kept.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Andrew.ps1" -Repair
if errorlevel 1 (
    echo.
    echo Recovery could not finish. Details are in the Andrew data folder.
    pause
)
