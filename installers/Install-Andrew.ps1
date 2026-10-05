param([string]$Destination = (Join-Path $env:LOCALAPPDATA 'Andrew'), [switch]$SkipModels)
$ErrorActionPreference='Stop'
$source = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $source 'server.py'))) { throw 'Extract the full Andrew package before running this installer.' }
$Destination=[IO.Path]::GetFullPath($Destination)
if ($Destination -eq [IO.Path]::GetPathRoot($Destination)) { throw 'Choose a dedicated Andrew folder.' }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
if ($source -ne $Destination) {
    $existingPython=Join-Path $Destination '.venv/Scripts/python.exe'
    if (Test-Path $existingPython) {
        & $existingPython (Join-Path $source 'update_merge.py') $source $Destination
        if ($LASTEXITCODE -eq 2) { Write-Host 'Your improvements are preserved. This update needs a merge; details are saved in data/update-status.json.'; return }
        if ($LASTEXITCODE -ne 0) { throw 'Update checks failed. Existing Andrew code was preserved.' }
    } elseif (Test-Path (Join-Path $Destination 'server.py')) {
        throw 'Use this existing installation’s Python runtime to merge the update. Existing improvements were not overwritten.'
    } else {
        Get-ChildItem -LiteralPath $source | Where-Object { $_.Name -notin @('.git','.venv','data','runtime','downloads','dist','__pycache__') } | Copy-Item -Destination $Destination -Recurse -Force
        $baseline=Join-Path $Destination 'data/update-baseline'
        New-Item -ItemType Directory -Force -Path $baseline | Out-Null
        Get-ChildItem -LiteralPath $source | Where-Object { $_.Name -notin @('.git','.venv','data','runtime','downloads','dist','__pycache__') } | Copy-Item -Destination $baseline -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $Destination 'data'),(Join-Path $Destination 'runtime'),(Join-Path $Destination 'data/tests') | Out-Null
$pythonPath = $null
try { $pythonPath = (& py -3.12 -c 'import sys; print(sys.executable)' 2>$null) } catch {}
if (-not $pythonPath) {
    $bootstrap = Join-Path $Destination 'runtime/python-installer.exe'
    Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $bootstrap
    $signature=Get-AuthenticodeSignature $bootstrap
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Python installer signature did not verify.' }
    $pythonDir=Join-Path $Destination 'runtime/python'
    $installer=Start-Process -FilePath $bootstrap -ArgumentList ('/quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_launcher=0 TargetDir="'+$pythonDir+'"') -Wait -PassThru -WindowStyle Hidden
    if ($installer.ExitCode -notin @(0,3010)) { throw 'Python setup did not finish.' }
    $pythonPath=Join-Path $pythonDir 'python.exe'
}
& $pythonPath -m venv (Join-Path $Destination '.venv')
if ($LASTEXITCODE -ne 0) { throw 'Could not create Andrew runtime.' }
$venv = Join-Path $Destination '.venv/Scripts/python.exe'
& $venv -m pip install -r (Join-Path $Destination 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependencies could not be installed. Check your internet connection and rerun.' }
if (-not $SkipModels) {
    Push-Location $Destination
    try {
        & $venv download_speech_models.py
        if ($LASTEXITCODE -ne 0) { throw 'Speech-model download failed; rerun to continue.' }
        & $venv setup_speech_upgrade.py
        if ($LASTEXITCODE -ne 0) { throw 'Recognition-model download failed; rerun to continue.' }
    } finally { Pop-Location }
}
$shell=New-Object -ComObject WScript.Shell
$shortcut=$shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Andrew.lnk'))
$shortcut.TargetPath='powershell.exe';$shortcut.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $Destination 'Start-Andrew.ps1')+'"';$shortcut.WorkingDirectory=$Destination;$shortcut.Save()
Write-Host 'Andrew installed. Open the desktop shortcut. Use Connections to select a signed-in AI account.'

