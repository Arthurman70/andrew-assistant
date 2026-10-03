param([switch]$Background)
$ErrorActionPreference = 'Stop'
$andrewRoot = $PSScriptRoot
$env:OLLAMA_MODELS = Join-Path $andrewRoot 'runtime\models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 }
catch {
    if (Test-Path (Join-Path $andrewRoot 'runtime\ollama\ollama.exe')) { Start-Process -FilePath (Join-Path $andrewRoot 'runtime\ollama\ollama.exe') -ArgumentList 'serve' -WorkingDirectory $andrewRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $andrewRoot 'data\ollama.log') -RedirectStandardError (Join-Path $andrewRoot 'data\ollama-error.log') }
}
try { $null = Invoke-RestMethod 'http://127.0.0.1:8765/api/status' -TimeoutSec 2 }
catch {
    Start-Process -FilePath (Join-Path $andrewRoot '.venv\Scripts\pythonw.exe') -ArgumentList ('"' + (Join-Path $andrewRoot 'server.py') + '"') -WorkingDirectory $andrewRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $andrewRoot 'data\server.log') -RedirectStandardError (Join-Path $andrewRoot 'data\server-error.log')
}
if (Test-Path (Join-Path $andrewRoot 'data\web-bridge.json')) { & (Join-Path $andrewRoot 'installers\Start-WebBridge.ps1') }
if (-not $Background) {
    $edge=Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
    if (Test-Path $edge) { Start-Process -FilePath $edge -ArgumentList '--app=http://127.0.0.1:8765/' } else { Start-Process 'http://127.0.0.1:8765' }
}
