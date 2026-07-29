@echo off
REM Launches the live local dashboard (swap data + orchestrator control panel)
REM and opens it in your default browser. Binds to localhost only.
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
if not exist ".venv\Scripts\python.exe" (
    echo Shared .venv not found at %~dp0.venv — run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Refuse to start a second dashboard on the same port -- besides just
REM failing to bind, a duplicate process also doubles up writes against
REM swaps.db and can cause "database is locked" errors for the scheduler.
netstat -ano | findstr /C:"127.0.0.1:8787" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo A dashboard already appears to be running on port 8787.
    echo Only run ONE instance of dashboard.bat at a time.
    echo Opening your existing dashboard in the browser instead...
    start "" http://127.0.0.1:8787
    pause
    exit /b 0
)
REM Give uvicorn a couple seconds to bind before opening the browser tab,
REM so it doesn't load before anything is listening.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8787"
cd dashboard
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8787
if %ERRORLEVEL% neq 0 (
    echo.
    echo Dashboard exited with an error ^(see above^). If it says the port is
    echo already in use or "access forbidden", another process or an unrelated
    echo Windows service already owns port 8787 — edit dashboard.bat and pick
    echo a different --port number ^(and update the URL above it^).
    pause
)
