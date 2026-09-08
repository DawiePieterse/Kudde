# Kudde - stop the server, and prove it actually stopped.
#
# "schtasks /end" ends the launcher (start_server.bat). The uvicorn process
# it spawned is a separate process and can outlive it, still listening on
# the port and still holding the database open - which would then block
# deleting the virtual environment during an uninstall. See Boord's
# stop_server.ps1, which this is adapted from, for the fuller history.
#
# Exit 0 means the port is genuinely free. Exit 1 means it is not, and the
# caller must not touch the database.

param(
    [int]$Port = 8030,
    [string]$TaskName = "Kudde Server",
    [int]$TimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$VenvDir = Join-Path (Join-Path $RepoRoot "backend") ".venv"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    $msg" -ForegroundColor Red }

function Get-PortListeners($port) {
    # Returns the listening connections on $port, or an empty array. Wrapped
    # because Get-NetTCPConnection throws rather than returning nothing when
    # there is no match, and "nothing is listening" is the success case here.
    try {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($null -eq $conns) { return @() }
        return @($conns)
    } catch {
        return @()
    }
}

function Wait-ForPortFree($port, $seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if ((Get-PortListeners $port).Count -eq 0) { return $true }
        Start-Sleep -Seconds 1
    }
    return (Get-PortListeners $port).Count -eq 0
}

# --- 1: end the scheduled task, if it is registered ---------------------------
cmd /c "schtasks /query /tn ""$TaskName"" >nul 2>&1"
if ($LASTEXITCODE -eq 0) {
    Write-Step "Stopping the '$TaskName' task..."
    cmd /c "schtasks /end /tn ""$TaskName"" >nul 2>&1"
} else {
    Write-Step "No '$TaskName' task registered - checking port $Port anyway..."
}

# --- 2: wait for the port to actually come free -------------------------------
if (Wait-ForPortFree $Port $TimeoutSeconds) {
    Write-Ok "Port $Port is free - the server is stopped."
    exit 0
}

# --- 3: stop what is left, but only if it is ours -----------------------------
# Deliberately NOT a blind kill of whatever owns the port - something else on
# the PC could have taken it, and killing an unrelated process is a worse
# outcome than refusing to proceed. Only processes running from this repo's
# own virtual environment are stopped.
Write-Warn "Port $Port is still held after $TimeoutSeconds seconds - looking at what holds it."

$refused = @()
foreach ($conn in Get-PortListeners $Port) {
    $ownerPid = $conn.OwningProcess
    $proc = $null
    try { $proc = Get-Process -Id $ownerPid -ErrorAction Stop } catch { }
    if ($null -eq $proc) { continue }

    $path = $null
    try { $path = $proc.Path } catch { }   # access denied on some processes

    if ($path -and $path.StartsWith($VenvDir, [StringComparison]::OrdinalIgnoreCase)) {
        try {
            Stop-Process -Id $ownerPid -Force -ErrorAction Stop
            Write-Ok "Stopped the leftover server process (PID $ownerPid, $path)"
        } catch {
            Write-Err "Could not stop PID $ownerPid ($path): $($_.Exception.Message)"
            $refused += "PID $ownerPid ($path)"
        }
    } else {
        $shown = if ($path) { $path } else { "$($proc.ProcessName) (path not readable)" }
        Write-Err "PID $ownerPid is not a Kudde process: $shown"
        $refused += "PID $ownerPid ($shown)"
    }
}

if ((Wait-ForPortFree $Port 10) -and $refused.Count -eq 0) {
    Write-Ok "Port $Port is free - the server is stopped."
    exit 0
}

Write-Host ""
Write-Err "Port $Port is STILL in use. The server has not been stopped."
foreach ($r in $refused) { Write-Err "  $r" }
Write-Err "Nothing that touches the database may run while this is true."
Write-Err "Stop that process by hand, then try again."
exit 1
