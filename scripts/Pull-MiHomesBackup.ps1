<#
.SYNOPSIS
    Copy the newest evo-dev database dump to this laptop — the off-box copy.

.DESCRIPTION
    evo-dev's nightly dumps (scripts/ops/pg-backup.sh) sit on the same disk as the database, so a
    lost container volume takes both. This pulls the newest verified dump (and its roles file) to
    $Dest, checks the byte count matches, and keeps the newest $Keep locally.

    Uses Windows OpenSSH, whose agent holds the evo-dev key (Git Bash's ssh cannot see it).
    Safe to re-run: a dump already copied is skipped.

    Schedule it daily (run once, in PowerShell):
      $a = New-ScheduledTaskAction -Execute powershell.exe -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\Pull-MiHomesBackup.ps1`""
      $t = New-ScheduledTaskTrigger -Daily -At 9am
      Register-ScheduledTask -TaskName 'MiHomes backup pull' -Action $a -Trigger $t -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
#>
param(
    [string]$Server = 'millena@107.170.18.108'  # the Droplet (mihomes.ai) since 2026-11-13; evo-dev retired,
    [string]$Dest = (Join-Path $env:USERPROFILE 'MiHomes-backups'),
    [int]$Keep = 30
)

$ErrorActionPreference = 'Stop'
$ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'
$scp = 'C:\Windows\System32\OpenSSH\scp.exe'
$remoteDir = '.mihomes/backups/pg/nightly'

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$log = Join-Path $Dest 'pull.log'
function Write-Log($msg) { "$(Get-Date -Format s) $msg" | Tee-Object -FilePath $log -Append }

$latest = (& $ssh $Server "cat $remoteDir/LATEST").Trim()
if ($LASTEXITCODE -ne 0 -or -not $latest) { Write-Log "FAIL: no LATEST on $Server"; exit 1 }

$name = Split-Path $latest -Leaf
$local = Join-Path $Dest $name
if (Test-Path $local) { Write-Log "already have $name"; exit 0 }

$remoteSize = [int64](& $ssh $Server "stat -c %s '$latest'").Trim()
& $scp -q "${Server}:$latest" $local
if ($LASTEXITCODE -ne 0) { Write-Log "FAIL: scp $name"; Remove-Item $local -ErrorAction SilentlyContinue; exit 1 }
if ((Get-Item $local).Length -ne $remoteSize) {
    Write-Log "FAIL: $name is $((Get-Item $local).Length) bytes, expected $remoteSize"
    Remove-Item $local; exit 1
}

$globals = $latest -replace 'mihomes-(\d{8}-\d{6})\.dump$', 'globals-$1.sql'
& $scp -q "${Server}:$globals" $Dest
if ($LASTEXITCODE -ne 0) { Write-Log "WARN: roles file $globals not copied" }

Get-ChildItem $Dest -Filter 'mihomes-*.dump' | Sort-Object Name -Descending |
    Select-Object -Skip $Keep | ForEach-Object {
        Remove-Item $_.FullName
        Remove-Item (Join-Path $Dest ($_.Name -replace '^mihomes-(.*)\.dump$', 'globals-$1.sql')) -ErrorAction SilentlyContinue
    }

Write-Log "ok: $name ($remoteSize bytes)"
