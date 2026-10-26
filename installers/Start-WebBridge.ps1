$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $root 'data/web-bridge.json'))) { return }
& (Join-Path $root '.venv/Scripts/python.exe') (Join-Path $root 'launcher.py') --bridge-only --background
if ($LASTEXITCODE -ne 0) { throw 'Andrew website bridge needs attention. See data/launcher-error.log.' }
