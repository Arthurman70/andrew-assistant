$ErrorActionPreference = 'Stop'
if (-not (Get-NetFirewallRule -DisplayName 'Andrew Pi relay TLS' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'Andrew Pi relay TLS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766 -RemoteAddress LocalSubnet -InterfaceAlias Ethernet -Profile Any | Out-Null
}
Get-NetFirewallRule -DisplayName 'Andrew Pi relay TLS' | Get-NetFirewallPortFilter | Select-Object Protocol,LocalPort | Out-File (Join-Path $PSScriptRoot 'data\firewall-ready.txt')
