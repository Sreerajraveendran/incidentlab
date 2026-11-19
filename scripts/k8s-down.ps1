<#
.SYNOPSIS
  Delete the MiniCloud kind cluster. Everything in it is removed, including the
  Postgres volume (it lives inside the node container).
#>
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$kind = Join-Path $root ".tools\kind.exe"
if (-not (Test-Path $kind)) { $kind = "kind" }

$answer = Read-Host "Delete the kind cluster 'minicloud' and all its data? Type 'minicloud' to confirm"
if ($answer -ne "minicloud") { "Cancelled."; exit 1 }
& $kind delete cluster --name minicloud
