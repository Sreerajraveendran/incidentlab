<#
.SYNOPSIS
  Send requests through NGINX and count which instance answered each one.
.EXAMPLE
  .\scripts\traffic.ps1 -Count 30
  .\scripts\traffic.ps1 -Path "/api/v1/slow?ms=200" -Count 20
#>
param(
    [int]$Count = 30,
    [string]$Path = "/api/v1/info",
    [int]$Port = 8088
)

$results = foreach ($i in 1..$Count) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$Path" -UseBasicParsing -TimeoutSec 15
        [pscustomobject]@{ Instance = $r.Headers["X-Pod-Name"]; Status = $r.StatusCode; Ms = $sw.ElapsedMilliseconds }
    } catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        [pscustomobject]@{ Instance = "(none)"; Status = $code; Ms = $sw.ElapsedMilliseconds }
    }
}

$results | Group-Object Instance | Sort-Object Name | ForEach-Object {
    [pscustomobject]@{
        Instance = $_.Name
        Requests = $_.Count
        Share    = "{0:P0}" -f ($_.Count / $Count)
        AvgMs    = [math]::Round(($_.Group | Measure-Object Ms -Average).Average)
    }
} | Format-Table -AutoSize

$failed = @($results | Where-Object { $_.Status -lt 200 -or $_.Status -ge 400 }).Count
"Failed: $failed of $Count"
