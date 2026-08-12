param(
  [int]$Port = 9222,
  [switch]$KeepExisting
)

$ErrorActionPreference = 'Stop'
if (-not $KeepExisting) {
  Get-Process TradingView -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}
$p = Get-AppxPackage -Name TradingView.Desktop |
  Sort-Object Version -Descending | Select-Object -First 1
if (-not $p) { throw 'TradingView.Desktop AppX package not found' }
$exe = Join-Path $p.InstallLocation 'TradingView.exe'
if (-not (Test-Path $exe)) { throw "TradingView executable not found: $exe" }
Start-Process -FilePath $exe -ArgumentList "--remote-debugging-port=$Port"

for ($i = 0; $i -lt 20; $i++) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/version" -UseBasicParsing
    Write-Output $r.Content
    exit 0
  } catch {
    Start-Sleep -Seconds 1
  }
}
Write-Error "CDP did not respond on port $Port after 20 seconds"
exit 2
