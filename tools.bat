@echo off
REM Launches the Tools module (Options Strategy Tool & Backtesting Tool),
REM served through the existing dashboard app, and opens it in your default
REM browser. Binds to localhost only. Mirrors dashboard.bat.
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
echo ============================================================
echo  Tools Module - Options Strategy Tool ^& Backtesting Tool
echo ============================================================
echo.
if not exist ".venv\Scripts\python.exe" (
    echo Shared .venv not found at %~dp0.venv — run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Catch a stale/incomplete venv (packages missing vs. requirements.txt)
REM *before* uvicorn starts, so the failure is one clear line instead of a
REM Python traceback plus misleading "port already in use" advice.
.venv\Scripts\python.exe -c "import fastapi, uvicorn, jinja2, slowapi" 2>nul
if errorlevel 1 (
    echo Your .venv is missing one or more required packages
    echo ^(fastapi / uvicorn / jinja2 / slowapi^). This usually means
    echo requirements.txt was updated after your venv was created.
    echo.
    echo Fix: .venv\Scripts\python.exe -m pip install -r requirements.txt
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
    echo Only run ONE instance of dashboard.bat / tools.bat at a time.
    echo Opening the Tools module in the browser instead...
    start "" http://127.0.0.1:8787/tools
    pause
    exit /b 0
)
REM Give uvicorn a couple seconds to bind before opening the browser tab,
REM so it doesn't load before anything is listening.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8787/tools"
echo Starting dashboard app (Tools is served through it)...
echo Tools module URL: http://localhost:8787/tools
echo.
cd dashboard
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8787
if %ERRORLEVEL% neq 0 (
    echo.
    echo Dashboard exited with an error ^(see above^). If it says the port is
    echo already in use or "access forbidden", another process or an unrelated
    echo Windows service already owns port 8787 — edit tools.bat and pick
    echo a different --port number ^(and update the URL above it^).
    pause
)
