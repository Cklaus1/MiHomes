# MiHomes Stop Script
# Stops the WhatsApp bridge and monitor processes.

Write-Host "Stopping MiHomes services..." -ForegroundColor Yellow

# Kill monitor
$monitor = Get-Process -Name "python*" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*whatsapp*monitor*" }
if ($monitor) {
    $monitor | Stop-Process -Force
    Write-Host "[monitor] Stopped." -ForegroundColor Green
} else {
    Write-Host "[monitor] Not running." -ForegroundColor DarkGray
}

# Kill node bridge
$bridge = Get-Process -Name "node*" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*mihomes*" -or $_.CommandLine -like "*bridge*" }
if ($bridge) {
    $bridge | Stop-Process -Force
    Write-Host "[bridge] Stopped." -ForegroundColor Green
} else {
    Write-Host "[bridge] Not running." -ForegroundColor DarkGray
}

Write-Host "Done." -ForegroundColor Green
