# Kudde - Windows server installer
#
# Installs Python if needed, installs Git and GnuPG so the server can pull
# signed updates, creates the virtual environment, installs dependencies,
# writes the launcher script, opens the Windows Firewall port, and registers
# the server to auto-start at boot (as SYSTEM, so no one needs to log in for
# it to start). Safe to re-run - each step checks what's already done.
# Adapted from Boord's install.ps1.
#
# The one thing it cannot do for you is decide WHICH signing key this server
# trusts - that fingerprint has to be typed in by a person, from a source
# they trust, rather than read out of the repository it is there to check.
# The last step prints how. See UPDATING.md.
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
# Gpg4win is what verifies signed releases in update_server.bat. Git for
# Windows bundles a gpg.exe, but it is unusable here: it keeps keys in a
# keyboxd daemon that the Git distribution does not ship, so importing a key
# fails with "probably not installed" and processes zero keys.
$Gpg4winUrl = "https://files.gpg4win.org/gpg4win-latest.exe"
$ReleaseKeyPath = Join-Path $RepoRoot "release-key.asc"
$FprFile = Join-Path $RepoRoot "data\release_key.fpr"

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

function Get-SmartAppControlState {
    # Windows 11's Smart App Control blocks executables whose publisher it
    # cannot verify - including installers downloaded by scripts like this
    # one. Worth knowing about before blaming ourselves for a failed install.
    #   0 = off   1 = on, enforcing   2 = evaluation mode   -1 = not present
    try {
        $v = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" `
                              -Name VerifiedAndReputablePolicyState -ErrorAction Stop
        return [int]$v.VerifiedAndReputablePolicyState
    } catch {
        return -1
    }
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

    # --- Step 2: Git (needed by update_server.bat, not by the server) ---
    Write-Step "Checking for Git..."
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        Write-Ok "Found Git at $($gitCmd.Source)"
    } else {
        Write-Warn "Git is not installed. The server itself will still run, but"
        Write-Warn "update_server.bat cannot fetch releases without it."
        Write-Warn "Install it from https://git-scm.com/download/win and re-run this."
    }

    # --- Step 3: GnuPG, for verifying signed releases ---
    Write-Step "Checking for GnuPG (verifies signed releases)..."
    $gpgExe = $null
    $gpgCmd = Get-Command gpg -ErrorAction SilentlyContinue
    # Git's bundled gpg is deliberately rejected - see $Gpg4winUrl above. It is
    # on PATH inside Git Bash and would otherwise look like a working answer.
    if ($gpgCmd -and $gpgCmd.Source -notlike "*\Git\usr\bin\*") {
        $gpgExe = $gpgCmd.Source
        Write-Ok "Found GnuPG at $gpgExe"
    } else {
        Write-Warn "No usable GnuPG found - installing Gpg4win..."
        $sac = Get-SmartAppControlState

        function Find-InstalledGpg {
            foreach ($candidate in @(
                (Join-Path $env:ProgramFiles "GnuPG\bin\gpg.exe"),
                (Join-Path ${env:ProgramFiles(x86)} "GnuPG\bin\gpg.exe")
            )) {
                if (Test-Path $candidate) { return $candidate }
            }
            return $null
        }

        try {
            # winget first. Smart App Control blocks executables a script
            # downloaded itself, but it treats Microsoft's own package manager
            # differently - so on a machine with SAC enforcing this is the path
            # that actually works. It is also not a way around the policy:
            # winget checks the package the same way any other install does.
            $winget = Get-Command winget -ErrorAction SilentlyContinue
            if ($winget) {
                Write-Warn "Trying winget..."
                cmd /c "winget install --id GnuPG.Gpg4win --silent --accept-package-agreements --accept-source-agreements >nul 2>&1"
                $gpgExe = Find-InstalledGpg
            }

            if (-not $gpgExe) {
                if ($sac -eq 1) {
                    Write-Warn "Smart App Control is on - a downloaded installer may be blocked."
                }
                Write-Warn "Downloading Gpg4win directly..."
                $gpgInstaller = Join-Path $env:TEMP "gpg4win-latest.exe"
                Invoke-WebRequest -Uri $Gpg4winUrl -OutFile $gpgInstaller -UseBasicParsing
                Write-Warn "Installing Gpg4win (this can take a minute)..."
                Start-Process -FilePath $gpgInstaller -ArgumentList "/S" -Wait
                Remove-Item $gpgInstaller -ErrorAction SilentlyContinue
                $gpgExe = Find-InstalledGpg
            }
            if ($gpgExe) {
                Write-Ok "Installed GnuPG at $gpgExe"
            } elseif ($sac -eq 1) {
                # Do not report this as our failure. Nothing was installed,
                # and the reason is a Windows policy, not a broken download.
                Write-Warn "Windows Smart App Control blocked the Gpg4win installer."
                Write-Warn "Nothing was installed. Install it by hand from https://gpg4win.org"
                Write-Warn "and allow it when Windows asks, then re-run this installer."
                Write-Warn "Do NOT switch Smart App Control off to get around this - on"
                Write-Warn "Windows 11 it cannot be switched back on without resetting Windows."
            } else {
                Write-Warn "Gpg4win ran but gpg.exe was not found in the usual places."
                Write-Warn "Install it by hand from https://gpg4win.org and re-run this installer."
            }
        } catch {
            Write-Warn "Could not install Gpg4win automatically: $($_.Exception.Message)"
            Write-Warn "Install it by hand from https://gpg4win.org, then re-run this installer."
        }
    }

    # Everything below talks to native tools that report success on stderr.
    # With $ErrorActionPreference = "Stop", Windows PowerShell turns a native
    # command's redirected stderr into a TERMINATING error - the same trap the
    # schtasks step further down documents. gpg is worse than most: "Total
    # number processed: 1" goes to stderr even on a clean import. So these run
    # through cmd, which keeps their stderr away from PowerShell, and the whole
    # block is non-fatal - a farm can import the key by hand.
    if ($gpgExe -and $gitCmd) {
        try {
            $gpgForGit = $gpgExe -replace '\\', '/'
            cmd /c "git config --global gpg.program ""$gpgForGit"" >nul 2>&1"
            Write-Ok "Told git to use this gpg for signature checks"
        } catch {
            Write-Warn "Could not set git's gpg.program - set it by hand (UPDATING.md)."
        }
    }

    if ($gpgExe -and (Test-Path $ReleaseKeyPath)) {
        # Importing the public key from the repo is safe: what actually decides
        # which releases are trusted is the fingerprint in data\release_key.fpr,
        # which lives outside the repo. A swapped key would not match it and
        # update_server.bat would refuse the release.
        #
        # Start-Process rather than a direct call or cmd /c. gpg reports even a
        # successful import on stderr, which PowerShell would turn into a
        # terminating error here; and cmd /c strips quotes from a command that
        # starts with one, which "C:\Program Files\..." does. Start-Process
        # sidesteps both and returns a real exit code.
        Write-Warn "Importing the release key (first gpg run can take a moment)..."
        try {
            $gpgLog = Join-Path $env:TEMP "kudde-gpg-import.log"
            # Not -Wait. GnuPG 2.5 starts keyboxd and gpg-agent on its first
            # run, and on a machine whose keyring has just been created that
            # start-up can hang indefinitely with its output redirected into a
            # non-interactive process. Importing the key is a convenience; it
            # must never be able to block the install.
            $proc = Start-Process -FilePath $gpgExe `
                -ArgumentList @("--batch", "--yes", "--import", $ReleaseKeyPath) `
                -NoNewWindow -PassThru `
                -RedirectStandardError $gpgLog -RedirectStandardOutput "$gpgLog.out"
            # Touching .Handle forces .NET to keep the process handle open.
            # Without it, Start-Process -PassThru used without -Wait leaves
            # .ExitCode reading as $null once the process has gone, and
            # `$null -eq 0` is false - so a perfectly good import gets reported
            # as "failed (exit )", with no exit code in the message because
            # there was never one to print.
            try { $null = $proc.Handle } catch { }
            if ($proc.WaitForExit(60000)) {
                $exit = $null
                try { $exit = $proc.ExitCode } catch { }
                if ($exit -eq 0) {
                    Write-Ok "Imported the Kudde release key"
                } elseif ($null -eq $exit) {
                    # Report honestly rather than guessing either way.
                    Write-Warn "gpg finished but Windows did not report its exit code."
                    Write-Warn "The import probably worked. Confirm with:"
                    Write-Warn "    ""$gpgExe"" --list-keys"
                } else {
                    Write-Warn "Importing release-key.asc failed (exit $exit) - see $gpgLog"
                    Write-Warn "Import it by hand before running update_server.bat."
                }
            } else {
                try { $proc.Kill() } catch { }
                Write-Warn "gpg did not finish within 60 seconds - skipped the key import."
                Write-Warn "This is usually gpg's first run initialising its keyring. Run this"
                Write-Warn "once in a Command Prompt, which lets it finish interactively:"
                Write-Warn "    ""$gpgExe"" --import release-key.asc"
                Write-Warn "Setup will carry on without it."
            }
            Remove-Item "$gpgLog.out" -ErrorAction SilentlyContinue
        } catch {
            Write-Warn "Could not import release-key.asc: $($_.Exception.Message)"
            Write-Warn "Import it by hand before running update_server.bat."
        }
    } elseif ($gpgExe) {
        Write-Warn "No release-key.asc in this folder, so nothing was imported."
        Write-Warn "update_server.bat will report the key as missing (not as tampering)"
        Write-Warn "until it is imported - see UPDATING.md."
    }

    # --- Step 4: Create the virtual environment ---
    Write-Step "Setting up the app's virtual environment..."
    if (-not (Test-Path $VenvDir)) {
        & $pythonExe -m venv $VenvDir
        Write-Ok "Created virtual environment"
    } else {
        Write-Ok "Virtual environment already exists"
    }
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    $venvPip = Join-Path $VenvDir "Scripts\pip.exe"

    # --- Step 5: Install dependencies ---
    Write-Step "Installing app dependencies (this can take a few minutes on first run)..."
    & $venvPip install --quiet --disable-pip-version-check -r (Join-Path $BackendDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to install dependencies. Check the internet connection and re-run this installer."
        exit 1
    }
    Write-Ok "Dependencies installed"

    # --- Step 6: Write the launcher script ---
    Write-Step "Creating the server launcher..."
    $launcherPath = Join-Path $RepoRoot "start_server.bat"
    $launcherContent = @"
@echo off
cd /d "$BackendDir"
"$venvPython" -m uvicorn main:app --host 0.0.0.0 --port $Port
"@
    Set-Content -Path $launcherPath -Value $launcherContent -Encoding ASCII
    Write-Ok "Created $launcherPath"

    # --- Step 7: Firewall rule ---
    Write-Step "Allowing the app through Windows Firewall..."
    netsh advfirewall firewall delete rule name="$FirewallRuleName" | Out-Null
    netsh advfirewall firewall add rule name="$FirewallRuleName" dir=in action=allow protocol=TCP localport=$Port | Out-Null
    Write-Ok "Firewall rule set for port $Port"

    # --- Step 8: Scheduled task (auto-start at boot, no login needed) ---
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

    # --- Step 9: Start it now ---
    Write-Step "Starting the server now..."
    schtasks /run /tn "$TaskName" | Out-Null

    # --- Step 10: Confirm it actually answers ---
    # Poll until it responds rather than sleeping a fixed few seconds and
    # declaring success - a server that died on startup (missing dependency,
    # port already in use) should say so, not look identical to a slow one.
    #
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

    # --- Step 11: Say how to reach it ---
    # No IPv4 address is printed any more, deliberately: this PC's LAN
    # address no longer opens anything, so printing it would send the farm
    # to an address that refuses them.

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

    # The one thing this installer cannot do for you. The fingerprint is what
    # decides which releases this server will accept, so it has to be typed in
    # by a person from a source they trust - not read from the repository,
    # which is the very thing it exists to check.
    if (-not (Test-Path $FprFile)) {
        Write-Host ""
        Write-Host "================================================" -ForegroundColor Yellow
        Write-Host " One step left: trust the release key" -ForegroundColor Yellow
        Write-Host "================================================" -ForegroundColor Yellow
        Write-Host " update_server.bat will refuse to install anything until this"
        Write-Host " server knows which signing key to trust. In this folder, run:"
        Write-Host ""
        Write-Host "     >data\release_key.fpr echo <FINGERPRINT>" -ForegroundColor Cyan
        Write-Host ""
        Write-Host " ...with the 40-character fingerprint from whoever maintains"
        Write-Host " this install. Type it with the redirect first, exactly as shown:"
        Write-Host " cmd reads a digit written immediately before a > as a file handle"
        Write-Host " number, so the more natural 'echo <FINGERPRINT>> file' would drop"
        Write-Host " the fingerprint's last character whenever it is a digit - and the"
        Write-Host " next update would then fail its signature check, which reads as"
        Write-Host " tampering rather than as a typo."
        Write-Host " See UPDATING.md, 'Trusting the release key'."
    } else {
        $fpr = (Get-Content $FprFile -TotalCount 1).Trim()
        Write-Ok "Release key fingerprint on file: $fpr"
    }

    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " Optional extras available in this folder" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host " These are separate, opt-in scripts - none of them ran just now."
    Write-Host " See UPDATING.md for details on each."
    Write-Host ""
    Write-Host " update_server.bat     - updates this server to the newest signed"
    Write-Host "                         release from GitHub and restarts it. This"
    Write-Host "                         is how you install an update; nothing"
    Write-Host "                         installs itself."
    Write-Host " setup_update_check.bat - registers a daily check that TELLS you"
    Write-Host "                         when a new release is out (the Admin app"
    Write-Host "                         says so). It installs nothing."
    Write-Host " uninstall.bat         - unregisters the server from Windows and"
    Write-Host "                         removes the virtual environment. Never"
    Write-Host "                         touches data\ or this folder."
} catch {
    Write-Host ""
    Write-Err "Something went wrong:"
    Write-Err $_.Exception.Message
    exit 1
}
