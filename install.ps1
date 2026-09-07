# Kudde - Windows server installer
#
# Installs Python if needed, creates the virtual environment, installs
# dependencies, writes the launcher script, opens the Windows Firewall port,
# and registers the server to auto-start at boot (as SYSTEM, so no one needs
# to log in for it to start). Safe to re-run - each step checks what's
# already done. Adapted from Boord's install.ps1, deliberately smaller:
# Kudde has no signed-release update mechanism yet, so there is no Git/GPG
# step here - see Boord's version for what that would add once Kudde ships
# updates to real farms.
#
# Run via install.bat, which handles the administrator-elevation prompt.
# Running this file directly (not via install.bat) requires an already
# elevated PowerShell window.

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$VenvDir = Join-Path $BackendDir ".venv"
# A distinct port from every other app that might already be on this farm
# PC. Boord defaults to 8000; Boord Owner and Boord Notes have been seen
# running as 8010 and 8020 on a real farm server - Kudde nearly collided
# with Boord Owner over 8010 on exactly that kind of shared box before this
# was caught, so 8030 continues the sequence rather than reusing a number
# any sibling app might already hold.
$Port = 8030
$TaskName = "Kudde Server"
$FirewallRuleName = "Kudde Server"
$PythonVersion = "3.11.9"
$PythonInstallerUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-Ok($msg) {
    Write-Host "    $msg" -ForegroundColor Green
}
function Write-Warn($msg) {
    Write-Host "    $msg" -ForegroundColor Yellow
}
function Write-Err($msg) {
    Write-Host "    $msg" -ForegroundColor Red
}

function Test-PythonOk($exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    try {
        $out = & $exe -c "import sys; print(sys.version_info[0]); print(sys.version_info[1]); print('64BIT' if sys.maxsize > 2**32 else '32BIT')" 2>$null
        if (-not $out -or $out.Count -lt 3) { return $false }
        $major = [int]$out[0]
        $minor = [int]$out[1]
        $bits = $out[2].Trim()
        return ($major -eq 3 -and $minor -ge 9 -and $bits -eq "64BIT")
    } catch {
        return $false
    }
}

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Err "This script needs to run as Administrator."
        Write-Err "Please run install.bat instead of this file directly - it handles that automatically."
        exit 1
    }

    Write-Host ""
    Write-Host "Kudde - Server Installer" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan

    # --- Step 1: Find or install Python ---
    Write-Step "Checking for Python 3.9+ (64-bit)..."
    $pythonExe = $null

    $existing = Get-Command python -ErrorAction SilentlyContinue
    if ($existing -and (Test-PythonOk $existing.Source)) {
        $pythonExe = $existing.Source
        Write-Ok "Found a compatible Python at $pythonExe"
    } else {
        $wellKnownPath = Join-Path $env:ProgramFiles "Python311\python.exe"
        if (Test-PythonOk $wellKnownPath) {
            $pythonExe = $wellKnownPath
            Write-Ok "Found a compatible Python at $pythonExe"
        } else {
            Write-Warn "No compatible 64-bit Python 3.9+ found - downloading Python $PythonVersion..."
            $installerPath = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
            Invoke-WebRequest -Uri $PythonInstallerUrl -OutFile $installerPath -UseBasicParsing
            Write-Warn "Installing Python (this can take a minute)..."
            Start-Process -FilePath $installerPath -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
            Remove-Item $installerPath -ErrorAction SilentlyContinue
            $pythonExe = $wellKnownPath
            if (-not (Test-PythonOk $pythonExe)) {
                Write-Err "Python installation could not be confirmed at $pythonExe."
                Write-Err "Please install Python 3.9+ (64-bit) manually from python.org and re-run this installer."
                exit 1
            }
            Write-Ok "Installed Python at $pythonExe"
        }
    }

    # --- Step 2: Create the virtual environment ---
    Write-Step "Setting up the app's virtual environment..."
    if (-not (Test-Path $VenvDir)) {
        & $pythonExe -m venv $VenvDir
        Write-Ok "Created virtual environment"
    } else {
        Write-Ok "Virtual environment already exists"
    }
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    $venvPip = Join-Path $VenvDir "Scripts\pip.exe"

    # --- Step 3: Install dependencies ---
    Write-Step "Installing app dependencies (this can take a few minutes on first run)..."
    & $venvPip install --quiet --disable-pip-version-check -r (Join-Path $BackendDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to install dependencies. Check the internet connection and re-run this installer."
        exit 1
    }
    Write-Ok "Dependencies installed"

    # --- Step 4: Write the launcher script ---
    Write-Step "Creating the server launcher..."
    $launcherPath = Join-Path $RepoRoot "start_server.bat"
    $launcherContent = @"
@echo off
cd /d "$BackendDir"
"$venvPython" -m uvicorn main:app --host 0.0.0.0 --port $Port
"@
    Set-Content -Path $launcherPath -Value $launcherContent -Encoding ASCII
    Write-Ok "Created $launcherPath"

    # --- Step 5: Firewall rule ---
    Write-Step "Allowing the app through Windows Firewall..."
    netsh advfirewall firewall delete rule name="$FirewallRuleName" | Out-Null
    netsh advfirewall firewall add rule name="$FirewallRuleName" dir=in action=allow protocol=TCP localport=$Port | Out-Null
    Write-Ok "Firewall rule set for port $Port"

    # --- Step 6: Scheduled task (auto-start at boot, no login needed) ---
    Write-Step "Registering the server to start automatically with Windows..."
    # Route schtasks through cmd so its stderr never reaches PowerShell.
    # $ErrorActionPreference = "Stop" (top of this file) turns a native
    # command's stderr into a TERMINATING error when it is redirected with
    # 2>&1 - and schtasks /query writes "ERROR: The system cannot find the
    # file specified." to stderr whenever the task is absent, which is the
    # normal state on a first-time install. See Boord's install.ps1, which
    # hit exactly this on new machines only.
    cmd /c "schtasks /query /tn ""$TaskName"" >nul 2>&1"
    if ($LASTEXITCODE -eq 0) {
        cmd /c "schtasks /end /tn ""$TaskName"" >nul 2>&1"
        Start-Sleep -Seconds 1
        schtasks /delete /tn "$TaskName" /f | Out-Null
    }
    schtasks /create /tn "$TaskName" /tr "`"$launcherPath`"" /sc onstart /ru SYSTEM /rl highest /f | Out-Null
    Write-Ok "Scheduled task '$TaskName' registered (runs at every startup, no one needs to log in)"

    # --- Step 7: Start it now ---
    Write-Step "Starting the server now..."
    schtasks /run /tn "$TaskName" | Out-Null

    # --- Step 8: Confirm it actually answers ---
    # Poll /healthz rather than /field/ or /admin/ - both now refuse anything
    # that didn't arrive over Tailscale, which this PC's own console never
    # has until Tailscale is set up (see the notice below). /healthz is the
    # one address that stays open everywhere, precisely so this check can
    # tell "the process died" apart from "Tailscale isn't set up yet".
    $serverUp = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-WebRequest -Uri "http://localhost:$Port/healthz" -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) { $serverUp = $true; break }
        } catch { }
    }
    if ($serverUp) {
        Write-Ok "Server is up and answering on port $Port"
    } else {
        Write-Warn "The server did not answer on port $Port within 20 seconds."
        Write-Warn "It may still be starting. If it never comes up, run start_server.bat"
        Write-Warn "directly in a window - errors are printed there rather than swallowed"
        Write-Warn "by the Scheduled Task."
    }

    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " Setup complete!" -ForegroundColor Green
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host " There is no sign-in, and no address on the farm wifi works - not"
    Write-Host " even http://localhost:$Port/ on this PC itself. Field and Admin"
    Write-Host " both answer only over Tailscale now."
    Write-Host ""
    Write-Host " Set up Tailscale on this PC (tailscale.com/download) and on every"
    Write-Host " phone or tablet that needs Field or Admin, signed into the same"
    Write-Host " tailnet. Once this PC is connected, run 'tailscale status' here to"
    Write-Host " find its https://<name>.<tailnet>.ts.net/ address, then open that"
    Write-Host " address with /field/ or /admin/ appended from any connected device -"
    Write-Host " including this one; localhost does not get an exemption."
    Write-Host ""
    Write-Host " The server will now start automatically every time this PC turns on."
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " Optional extras available in this folder" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " uninstall.bat - unregisters the server from Windows and removes"
    Write-Host "                 the virtual environment. Never touches data\ or"
    Write-Host "                 this folder."
} catch {
    Write-Host ""
    Write-Err "Something went wrong:"
    Write-Err $_.Exception.Message
    exit 1
}
