param([switch]$Background, [switch]$Repair)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Andrew's runtime is missing. Run the Andrew installer to repair it." -ForegroundColor Red
    exit 1
}
$launchArgs = @((Join-Path $PSScriptRoot 'launcher.py'))
if ($Background) { $launchArgs += '--background' }
if ($Repair) { $launchArgs += '--repair' }
& $python @launchArgs
exit $LASTEXITCODE
