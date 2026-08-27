@echo off
REM Launches the standalone swap-data browser and opens it in your default
REM browser. Binds to localhost only. Split out of dashboard.bat 2026-08-27
REM so the main dashboard's boot never has to touch swaps.db -- see
REM docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
if not exist ".venv\Scripts\python.exe" (
    echo Shared .venv not found at %~dp0.venv — run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -c "import fastapi, uvicorn, jinja2" 2>nul
if errorlevel 1 (
    echo Your .venv is missing one or more required packages
    echo ^(fastapi / uvicorn / jinja2^). This usually means
    echo requirements.txt was updated after your venv was created.
    echo.
    echo Fix: .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Refuse to start a second swaps dashboard on the same port -- same reason
REM dashboard.bat refuses to double-launch on 8787: a duplicate process
REM doubles up reads against swaps.db.
netstat -ano | findstr /C:"127.0.0.1:8788" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo A swaps dashboard already appears to be running on port 8788.
    echo Only run ONE instance of swaps_dashboard.bat at a time.
    echo Opening your existing swaps dashboard in the browser instead...
    start "" http://127.0.0.1:8788/swaps
    pause
    exit /b 0
)
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8788/swaps"
.venv\Scripts\python.exe -m uvicorn swaps_dashboard.app:app --host 127.0.0.1 --port 8788
if %ERRORLEVEL% neq 0 (
    echo.
    echo Swaps dashboard exited with an error ^(see above^). If it says the port
    echo is already in use, another process already owns port 8788 — edit
    echo swaps_dashboard.bat and pick a different --port number ^(and update the
    echo URL above it^).
    pause
)
