param([string]$AndrewRoot = (Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference = 'Stop'
$AndrewRoot = (Resolve-Path -LiteralPath $AndrewRoot).Path
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = Join-Path ([Environment]::GetFolderPath('Programs')) 'Andrew'
New-Item -ItemType Directory -Force -Path $programs | Out-Null
foreach ($folder in @($desktop, $programs)) {
    foreach ($name in @('Andrew', 'Repair Andrew')) {
        $shortcut = $shell.CreateShortcut((Join-Path $folder ($name + '.lnk')))
        $shortcut.TargetPath = Join-Path $env:WINDIR 'System32/cmd.exe'
        $command = if ($name -eq 'Andrew') { 'Start Andrew.cmd' } else { 'Repair Andrew.cmd' }
        $shortcut.Arguments = '/d /c ""' + (Join-Path $AndrewRoot $command) + '""'
        $shortcut.WorkingDirectory = $AndrewRoot
        $shortcut.Description = if ($name -eq 'Andrew') { 'Turn Andrew on and open the app' } else { 'Restore Andrew if it has stopped or is unresponsive' }
        $shortcut.Save()
    }
}
Write-Host 'Andrew and Repair Andrew are available on your desktop and in the Start menu.'
