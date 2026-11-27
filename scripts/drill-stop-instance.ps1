<#
.SYNOPSIS
  Failure drill (Compose stage): stop one app instance under traffic, then bring it back.
  Expected: no failed requests while it is down; it rejoins the rotation after restart.
#>
param(
    [ValidateSet("app-1", "app-2", "app-3")]
    [string]$Instance = "app-2",
    [int]$Port = 8088
)

$ErrorActionPreference = "Stop"
$compose = Join-Path $PSScriptRoot "..\deploy\compose"
$traffic = Join-Path $PSScriptRoot "traffic.ps1"

$answer = Read-Host "Stop $Instance in the local IncidentLab Compose stack? Type the instance name to confirm"
if ($answer -ne $Instance) { "Cancelled."; exit 1 }

Push-Location $compose
try {
    "== Baseline"
    & $traffic -Count 30 -Port $Port

    "== Stopping $Instance at $(Get-Date -Format o)"
    docker compose stop $Instance | Out-Null
    & $traffic -Count 30 -Port $Port

    "== NGINX retries (two upstreams on one line = failover)"
    docker compose logs nginx --no-log-prefix --since 2m | Select-String "upstream=.*, " | Select-Object -Last 5

    "== Starting $Instance at $(Get-Date -Format o)"
    docker compose start $Instance | Out-Null
    Start-Sleep -Seconds 12
    & $traffic -Count 30 -Port $Port
} finally {
    Pop-Location
}
