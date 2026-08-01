<#
setup_bgutil_autostart.ps1

Run this ONCE. It registers the bgutil PO-token server (used by the YouTube
sentiment scanner) as a Windows Scheduled Task that starts automatically
every time you log in, running hidden in the background. After this,
you never need to manually run "node build\main.js" again -- the server
is just always there, the same way you'd expect a background service to
behave.

Usage (run from a normal PowerShell window, not necessarily elevated --
"At log on" tasks for the current user don't require admin rights):

    cd C:\Users\bottl\FinancialDevelopment
    .\setup_bgutil_autostart.ps1

To check it's running at any time:
    Invoke-WebRequest http://127.0.0.1:4416/ping

To stop it (e.g. to free the port):
    Get-ScheduledTask -TaskName "bgutil-pot-server" | Stop-ScheduledTask

To remove the autostart entirely:
    Unregister-ScheduledTask -TaskName "bgutil-pot-server" -Confirm:$false
#>

$ErrorActionPreference = "Stop"

$ServerDir = "C:\Users\bottl\bgutil-ytdlp-pot-provider\server"
$MainJs    = Join-Path $ServerDir "build\main.js"
$TaskName  = "bgutil-pot-server"

Write-Host "============================================================"
Write-Host " bgutil PO-token server -- permanent autostart setup"
Write-Host "============================================================"

if (-not (Test-Path $MainJs)) {
    Write-Host ""
    Write-Host "ERROR: $MainJs does not exist." -ForegroundColor Red
    Write-Host "You need to clone and build the server first:"
    Write-Host "  cd C:\Users\bottl"
    Write-Host "  git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
    Write-Host "  cd bgutil-ytdlp-pot-provider\server"
    Write-Host "  npm ci"
    Write-Host "  npx tsc"
    Write-Host "Then re-run this script."
    exit 1
}

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Host "ERROR: 'node' isn't on PATH. Install Node.js first (nodejs.org), then re-run." -ForegroundColor Red
    exit 1
}
$NodePath = $node.Source

# Remove any previous registration so re-running this script is safe/idempotent.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Existing '$TaskName' task found -- replacing it."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action    = New-ScheduledTaskAction -Execute $NodePath -Argument "`"$MainJs`"" -WorkingDirectory $ServerDir
$Trigger   = New-ScheduledTaskTrigger -AtLogOn
$Settings  = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal -Description "Auto-starts the bgutil YouTube PO-token server at login for the sentiment scanner." `
    | Out-Null

Write-Host "Registered scheduled task '$TaskName' (starts hidden at every login, auto-restarts up to 3x if it crashes)."
Write-Host ""
Write-Host "Starting it right now so you don't have to log out/in..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2

try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:4416/ping" -TimeoutSec 3 -UseBasicParsing
    Write-Host ""
    Write-Host "SUCCESS -- server responded: $($resp.Content)" -ForegroundColor Green
    Write-Host "It will now start automatically every time you log in. You're done with this -- forget it exists."
} catch {
    Write-Host ""
    Write-Host "Task was registered and started, but the ping check failed: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "Give it a few more seconds and check manually: Invoke-WebRequest http://127.0.0.1:4416/ping"
}
