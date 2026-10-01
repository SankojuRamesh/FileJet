<#
.SYNOPSIS
  Removes the MediaRush server tasks and firewall rules (Administrator PowerShell).

      powershell -ExecutionPolicy Bypass -File deploy\windows\uninstall.ps1                 # keep files + database
      powershell -ExecutionPolicy Bypass -File deploy\windows\uninstall.ps1 -RemoveFiles    # delete C:\MediaRush too
#>
param([string]$InstallDir = "C:\MediaRush", [switch]$RemoveFiles)
$ErrorActionPreference = "Continue"
foreach ($t in @("MediaRush Server", "MediaRush HTTPS")) {
    Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*serve.py*" -and $_.CommandLine -like "*$InstallDir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-NetFirewallRule -DisplayName "MediaRush *" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($RemoveFiles -and (Test-Path $InstallDir)) {
    Remove-Item -Recurse -Force $InstallDir
    Write-Host "Removed $InstallDir (including the database)."
} else {
    Write-Host "Tasks and firewall rules removed. Files and database kept in $InstallDir."
}
