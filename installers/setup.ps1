$ErrorActionPreference='Stop'
$stage=Join-Path ([IO.Path]::GetTempPath()) ('Andrew-Setup-'+[guid]::NewGuid())
New-Item -ItemType Directory -Path $stage | Out-Null
Expand-Archive -LiteralPath (Join-Path $PSScriptRoot 'Andrew-Windows.zip') -DestinationPath $stage
& (Join-Path $stage 'Andrew/installers/Install-Andrew.ps1')
Write-Host 'Setup complete. Open Andrew from the desktop shortcut.'
