$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $root 'data/web-bridge.json'))) { return }
try { $existing=Get-Content (Join-Path $root 'data/web-bridge.pid') -ErrorAction Stop; $process=Get-Process -Id $existing -ErrorAction Stop; if ($process.ProcessName -eq 'pythonw') { return } } catch {}
$process=Start-Process -FilePath (Join-Path $root '.venv/Scripts/pythonw.exe') -ArgumentList ('"'+(Join-Path $root 'bridge_runner.py')+'"') -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $root 'data/web-bridge.log') -RedirectStandardError (Join-Path $root 'data/web-bridge-error.log')
$process.Id | Set-Content (Join-Path $root 'data/web-bridge.pid')
