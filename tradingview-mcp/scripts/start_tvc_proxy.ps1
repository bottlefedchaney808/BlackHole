param(
  [int]$Port = 19222,
  [int]$TargetPort = 9222,
  [string]$ProxyScript = (Join-Path $PSScriptRoot 'tvc_proxy.py')
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $ProxyScript)) {
  throw "Proxy script not found: $ProxyScript"
}

# Stage beside the Windows Python process so a WSL/UNC source path is safe.
$staged = Join-Path $env:TEMP 'tvc_proxy.py'
Copy-Item -LiteralPath $ProxyScript -Destination $staged -Force
Get-Process py,python -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*tvc_proxy.py*' } |
  Stop-Process -Force -ErrorAction SilentlyContinue

$python = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command python.exe -ErrorAction Stop).Source }
Start-Process -FilePath $python -ArgumentList '-3', $staged, '--port', $Port, '--target-port', $TargetPort -WindowStyle Hidden

for ($i = 0; $i -lt 10; $i++) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/version" -UseBasicParsing
    Write-Output $r.Content
    exit 0
  } catch {
    Start-Sleep -Seconds 1
  }
}
Write-Error "Proxy did not respond on port $Port after 10 seconds"
exit 2
