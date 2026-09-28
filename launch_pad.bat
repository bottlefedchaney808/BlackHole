@echo off
REM The launch pad: one card per trading sleeve, with Launch / Stop buttons.
REM Double-click this file. It starts the dock on 127.0.0.1:8792 and opens it.
REM
REM This is NOT the chart (chart_app.bat, :8791). They are separate processes;
REM starting one does not start the other.
cd /d "%~dp0"
REM Hermes sessions leak PYTHONPATH/PYTHONHOME into the project venv and pull
REM in the wrong site-packages. Clear both before touching .venv.
set PYTHONPATH=
set PYTHONHOME=
set VIRTUAL_ENV=

if not exist ".venv\Scripts\python.exe" (
  echo [launch_pad] .venv not found. Run: python -m venv .venv
  echo               then: .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

REM Refuse to double-launch: a second dock cannot bind 8792, and two docks
REM writing artifacts\launch_dock.json would race each other's card state.
powershell -NoProfile -Command "if ((Test-NetConnection -ComputerName 127.0.0.1 -Port 8792 -InformationLevel Quiet -WarningAction SilentlyContinue)) { exit 1 } else { exit 0 }" >nul 2>&1
if errorlevel 1 (
  echo [launch_pad] already running on 127.0.0.1:8792 - opening it.
  start "" "http://127.0.0.1:8792/"
  exit /b 0
)

echo [launch_pad] starting dock on http://127.0.0.1:8792 ...
start "launch-pad" /min ".venv\Scripts\python.exe" -m launch_dock.server

REM Wait for the port instead of a fixed sleep, so the browser never opens
REM onto a connection error.
powershell -NoProfile -Command "$d=0; while ($d -lt 20 -and -not (Test-NetConnection -ComputerName 127.0.0.1 -Port 8792 -InformationLevel Quiet -WarningAction SilentlyContinue)) { Start-Sleep -Milliseconds 500; $d++ }" >nul 2>&1

start "" "http://127.0.0.1:8792/"
echo [launch_pad] open. Close the minimised "launch-pad" window to stop the dock.
echo               Stopping the dock does NOT stop a running sleeve.
