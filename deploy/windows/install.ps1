<#
.SYNOPSIS
  Installs the FileJet server (cloud + signaling) on Windows - no Docker.

.DESCRIPTION
  Run in an *Administrator* PowerShell from the project folder:

      powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1
      powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Address 192.168.1.20
      powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Domain filejet.live

  What it does:
    1. copies the server files to -InstallDir (default C:\FileJet)
    2. creates a Python virtual environment and installs the requirements
    3. writes server.env with random secrets (kept if it already exists)
    4. prepares the database (SQLite in <InstallDir>\data)
    5. registers the "FileJet Server" task: starts at boot, restarts on failure
    6. opens the Windows Firewall ports
    7. -Domain only: writes a Caddyfile (plain HTTP; -Https for a certificate) and starts Caddy if installed

  Re-running it updates the files and keeps server.env and the database.
#>
param(
    [string]$InstallDir = "C:\FileJet",
    [string]$Address = "",          # IP or host name users connect to (office network, plain HTTP)
    [string]$Domain = "",           # public domain name -> HTTPS via Caddy
    [int]$CloudPort = 8000,
    [int]$SignalPort = 8765,
    [int]$ReflectorPort = 8766,
    [switch]$Https,                 # with -Domain: get an HTTPS certificate (default: plain HTTP)
    [switch]$SkipService,
    [switch]$SkipFirewall
)
$ErrorActionPreference = "Stop"
$Source = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function NewSecret { $b = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); return ([Convert]::ToBase64String($b) -replace '[+/=]', 'x') }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin -and -not ($SkipService -and $SkipFirewall)) {
    throw "Please run this in an Administrator PowerShell (or use -SkipService -SkipFirewall)."
}

# ---------------------------------------------------------------- 1. Python
Step "Checking Python"
$py = $null
foreach ($cand in @("py -3", "python")) {
    try {
        $v = & cmd /c "$cand -c `"import sys;print('%d.%d' % sys.version_info[:2])`"" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { $parts = $v.Trim().Split("."); if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) { $py = $cand; break } }
    } catch { }
}
if (-not $py) { throw "Python 3.10 or newer is required: https://www.python.org/downloads/ (tick 'Add to PATH')." }
Write-Host "Using: $py ($v)"

# ---------------------------------------------------------------- 2. files
Step "Copying server files to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir, "$InstallDir\data" | Out-Null
foreach ($item in @("cloud", "server", "serve.py", "requirements.txt", "deploy")) {
    $src = Join-Path $Source $item
    if (Test-Path $src) {
        if ((Get-Item $src).PSIsContainer) {
            robocopy $src (Join-Path $InstallDir $item) /MIR /XD __pycache__ staticfiles /XF db.sqlite3 *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "copy of $item failed" }
        } else { Copy-Item $src $InstallDir -Force }
    }
}
$global:LASTEXITCODE = 0

# ---------------------------------------------------------------- 3. venv + packages
Step "Creating the Python environment (first time takes a minute)"
$venvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & cmd /c "$py -m venv `"$InstallDir\.venv`""; if ($LASTEXITCODE -ne 0) { throw "venv failed" } }
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r "$InstallDir\cloud\requirements.txt" -r "$InstallDir\server\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# ---------------------------------------------------------------- 4. settings
$envFile = Join-Path $InstallDir "server.env"
if (-not $Address) {
    $Address = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -ne "WellKnown" } |
        Sort-Object -Property InterfaceMetric | Select-Object -First 1).IPAddress
    if (-not $Address) { $Address = $env:COMPUTERNAME }
}
if ($Domain) {
    if ($Https) { $cloudUrl = "https://$Domain/"; $signalUrl = "wss://$Domain/ws"; $https = "1" }
    else { $cloudUrl = "http://$Domain/"; $signalUrl = "ws://$Domain/ws"; $https = "0" }
    $hosts = "$Domain,www.$Domain,localhost,127.0.0.1"; $cloudHost = "127.0.0.1"
} else {
    $cloudUrl = "http://${Address}:$CloudPort/"; $signalUrl = "ws://${Address}:$SignalPort/ws"; $hosts = "$Address,$env:COMPUTERNAME,localhost,127.0.0.1"; $https = "0"; $cloudHost = "0.0.0.0"
}
if (Test-Path $envFile) {
    Step "Keeping existing settings: $envFile"
} else {
    Step "Writing settings: $envFile"
    $content = @"
# FileJet server settings (written by install.ps1). Restart the "FileJet Server" task after changes.
DJANGO_SECRET_KEY=$(NewSecret)
P2P_CLOUD_JWT_SECRET=$(NewSecret)
DJANGO_ALLOWED_HOSTS=$hosts
DJANGO_CSRF_TRUSTED_ORIGINS=$($cloudUrl.TrimEnd('/'))
DJANGO_HTTPS=$https
DJANGO_SQLITE_PATH=$InstallDir\data\mediarush.sqlite3
# DATABASE_URL=postgres://user:password@127.0.0.1:5432/mediarush   (optional instead of SQLite)
CLOUD_PUBLIC_URL=$cloudUrl
P2P_SIGNALING_URL=$signalUrl
P2P_PUBLIC_HOST=$(if ($Domain) { $Domain } else { $Address })
P2P_TRUST_PROXY=$(if ($Domain) { "1" } else { "0" })
HOST=0.0.0.0
CLOUD_HOST=$cloudHost
CLOUD_PORT=$CloudPort
SIGNAL_PORT=$SignalPort
REFLECTOR_PORT=$ReflectorPort
BILLING_PROVIDER=dummy

# E-mail: until SMTP is set, invitations are shown on the web under "Inbox" (and in the log).
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
SHOW_EMAIL_OUTBOX=1
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# EMAIL_HOST=smtp.example.com
# EMAIL_PORT=587
# EMAIL_HOST_USER=
# EMAIL_HOST_PASSWORD=
# EMAIL_USE_TLS=1
# DEFAULT_FROM_EMAIL=FileJet <no-reply@example.com>
# SHOW_EMAIL_OUTBOX=0
"@
    Set-Content -Path $envFile -Value $content -Encoding ascii
}

# ---------------------------------------------------------------- 5. database
Step "Preparing the database"
& $venvPy "$InstallDir\serve.py" --env $envFile --check
if ($LASTEXITCODE -ne 0) { throw "settings/database check failed" }

# run script used by the task (logs to data\server.log)
$runCmd = Join-Path $InstallDir "run-server.cmd"
Set-Content -Path $runCmd -Encoding ascii -Value "@echo off`r`ncd /d `"$InstallDir`"`r`n`"$venvPy`" serve.py --env `"$envFile`" >> `"$InstallDir\data\server.log`" 2>&1"

# ---------------------------------------------------------------- 6. service (scheduled task)
if (-not $SkipService) {
    Step "Registering the 'FileJet Server' task (starts at boot, restarts on failure)"
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$runCmd`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Unregister-ScheduledTask -TaskName "FileJet Server" -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName "FileJet Server" -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Stop-ScheduledTask -TaskName "FileJet Server" -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName "FileJet Server"
}

# ---------------------------------------------------------------- 7. firewall
if (-not $SkipFirewall) {
    Step "Opening Windows Firewall ports"
    Get-NetFirewallRule -DisplayName "FileJet *" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if ($Domain -and $Https) { $ports = @(80, 443, $ReflectorPort) } elseif ($Domain) { $ports = @(80, $ReflectorPort) } else { $ports = @($CloudPort, $SignalPort, $ReflectorPort) }
    New-NetFirewallRule -DisplayName "FileJet Server" -Direction Inbound -Protocol TCP -LocalPort $ports -Action Allow | Out-Null
    Write-Host ("Allowed inbound TCP " + ($ports -join ", "))
}

# ---------------------------------------------------------------- 8. HTTPS (domain only)
if ($Domain) {
    Step "Web proxy (Caddy) for $Domain"
    $caddyfile = Join-Path $InstallDir "Caddyfile"
    if ($Https) { $site = $Domain } else { $site = "http://$Domain" }
    Set-Content -Path $caddyfile -Encoding ascii -Value @"
$site {
	encode zstd gzip
	reverse_proxy /ws 127.0.0.1:$SignalPort
	reverse_proxy /ws/presence 127.0.0.1:$SignalPort
	reverse_proxy /healthz 127.0.0.1:$SignalPort
	reverse_proxy 127.0.0.1:$CloudPort
}
"@
    $caddy = (Get-Command caddy -ErrorAction SilentlyContinue).Source
    if (-not $caddy -and (Test-Path "$InstallDir\caddy.exe")) { $caddy = "$InstallDir\caddy.exe" }
    if ($caddy -and -not $SkipService) {
        $a = New-ScheduledTaskAction -Execute $caddy -Argument "run --config `"$caddyfile`"" -WorkingDirectory $InstallDir
        Unregister-ScheduledTask -TaskName "FileJet HTTPS" -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName "FileJet HTTPS" -Action $a -Trigger (New-ScheduledTaskTrigger -AtStartup) `
            -Settings $settings -Principal $principal | Out-Null
        Start-ScheduledTask -TaskName "FileJet HTTPS"
        Write-Host "Caddy started for $site (DNS for $Domain must point to this server)."
    } else {
        Write-Host "Install Caddy (web proxy), then re-run this script:  winget install CaddyServer.Caddy" -ForegroundColor Yellow
        Write-Host "  (or put caddy.exe from https://caddyserver.com/download into $InstallDir)"
    }
}

# ---------------------------------------------------------------- done
Step "Done"
Write-Host "Cloud URL for the FileJet app:  $cloudUrl" -ForegroundColor Green
Write-Host "Web dashboard:                    $cloudUrl"
Write-Host "Settings:                         $envFile"
Write-Host "Log:                              $InstallDir\data\server.log"
Write-Host "Admin account (optional):         & `"$venvPy`" `"$InstallDir\serve.py`" --env `"$envFile`" manage createsuperuser"
Write-Host "Check:                            ${cloudUrl}api/config/"
