# MiHomes Startup Script
# Starts the WhatsApp bridge and AI monitor as background processes.
# Usage: .\scripts\Start-MiHomes.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BridgeDir = Join-Path $ProjectRoot "bridge"
$LogDir = Join-Path $env:USERPROFILE ".mihomes"

# Resolve NVIDIA API key from config DB, fallback to env var
$NvidiaKey = $env:NVIDIA_API_KEY
if (-not $NvidiaKey) {
    try {
        $NvidiaKey = python -c "
from mihomes.db import get_session
from mihomes.services.config_service import get_config
with get_session() as s:
    print(get_config(s, 'ai.nim_api_key') or '', end='')
" 2>$null
    } catch {}
}

if (-not $NvidiaKey) {
    Write-Host "WARNING: NVIDIA_API_KEY not found in environment or config. Monitor will start without AI." -ForegroundColor Yellow
    # SPEC-003 U1: stored credentials are encrypted, keyed from MIHOMES_SECRET_KEY. The lookup
    # above is a separate `python` process with its own environment, and it is wrapped in
    # `2>$null` + `catch {}` -- so a missing key looks identical to a missing credential. Naming
    # the likely cause turns a silent degradation into a fixable one.
    if (-not $env:MIHOMES_SECRET_KEY) {
        Write-Host "  MIHOMES_SECRET_KEY is not set in this shell, so an encrypted key in the config DB could not be read." -ForegroundColor Yellow
        Write-Host "  Set it (see 'mihomes config generate-key') and re-run, or export NVIDIA_API_KEY directly." -ForegroundColor Yellow
    }
}

# --- Check if bridge is already running ---
$bridgeRunning = $false
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:7867/status" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    $bridgeRunning = $true
    Write-Host "[bridge] Already running." -ForegroundColor Green
} catch {}

if (-not $bridgeRunning) {
    Write-Host "[bridge] Starting WhatsApp bridge..." -ForegroundColor Cyan
    Start-Process -FilePath "npm" `
        -ArgumentList "start" `
        -WorkingDirectory $BridgeDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput "$LogDir\bridge.log" `
        -RedirectStandardError "$LogDir\bridge-error.log"
    # Wait for bridge to come up
    $attempts = 0
    while ($attempts -lt 20) {
        Start-Sleep -Seconds 2
        try {
            Invoke-WebRequest -Uri "http://localhost:7867/status" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop | Out-Null
            Write-Host "[bridge] Connected." -ForegroundColor Green
            break
        } catch {}
        $attempts++
    }
    if ($attempts -eq 20) {
        Write-Host "[bridge] WARNING: Bridge did not respond after 40s. Check the bridge window." -ForegroundColor Yellow
    }
}

# --- Check if monitor is already running ---
$monitorRunning = Get-Process -Name "python*" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*whatsapp*monitor*" }

if ($monitorRunning) {
    Write-Host "[monitor] Already running (PID $($monitorRunning.Id))." -ForegroundColor Green
} else {
    Write-Host "[monitor] Starting WhatsApp monitor..." -ForegroundColor Cyan
    $monitorCmd = "NVIDIA_API_KEY=$NvidiaKey python -c `"from mihomes.cli import app; app()`" whatsapp monitor --property belle-estate"
    $monitorProc = Start-Process -FilePath "python" `
        -ArgumentList "-c `"from mihomes.cli import app; app()`" whatsapp monitor --property $monitor_property" `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -PassThru `
        -Environment @{ NVIDIA_API_KEY = $NvidiaKey }
    $monitorProc.Id | Set-Content "$LogDir\monitor.pid"
    Write-Host "[monitor] Started (PID $($monitorProc.Id))." -ForegroundColor Green
}

Write-Host ""
Write-Host "MiHomes is running." -ForegroundColor Green
Write-Host "  Bridge:  http://localhost:7867" -ForegroundColor DarkGray
Write-Host "  Logs:    $LogDir\monitor.log" -ForegroundColor DarkGray
Write-Host ""
Write-Host "To stop: close the bridge and monitor windows, or run Stop-MiHomes.ps1" -ForegroundColor DarkGray
