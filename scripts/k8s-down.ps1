<#
.SYNOPSIS
  Delete the IncidentLab kind cluster. Everything in it is removed, including the
  Postgres volume (it lives inside the node container).
#>
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$kind = Join-Path $root ".tools\kind.exe"
if (-not (Test-Path $kind)) { $kind = "kind" }

$answer = Read-Host "Delete the kind cluster 'incidentlab' and all its data? Type 'incidentlab' to confirm"
if ($answer -ne "incidentlab") { "Cancelled."; exit 1 }
& $kind delete cluster --name incidentlab
