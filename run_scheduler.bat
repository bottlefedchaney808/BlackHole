@echo off
REM Starts the DTCC live poller. Leave this window open — it polls DTCC's
REM public API every POLL_INTERVAL_MINUTES (default 5) and loads new swap
REM trades automatically. Ctrl+C to stop.
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
REM Point swaps queries at the OneDrive live book (323 GB, ~71M rows).
REM If SWAPS_DB_PATH is already set (e.g. in your user env), it wins.
if not defined SWAPS_DB_PATH set SWAPS_DB_PATH=C:\Users\bottl\OneDrive\Stocks\Swaps\swaps.db
if not exist ".venv\Scripts\python.exe" (
    echo Shared .venv not found at %~dp0.venv — run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Only one scheduler may write to swaps.db at a time -- a second instance
REM causes "database is locked" errors that silently stall ingestion progress.
REM mkdir is atomic, so this is a safe single-instance lock.
mkdir "%~dp0.scheduler.lock" 2>nul
if errorlevel 1 (
    echo A scheduler already appears to be running ^(lock folder .scheduler.lock exists^).
    echo Only run ONE instance of run_scheduler.bat at a time -- a second one will
    echo silently corrupt ingestion progress by racing the first for database writes.
    echo.
    echo If you're sure nothing is actually running ^(e.g. it crashed without
    echo cleaning up^), delete the ".scheduler.lock" folder and try again.
    echo.
    pause
    exit /b 1
)
.venv\Scripts\python.exe scheduled_ingest.py --start-scheduler
set SCHED_EXIT=%ERRORLEVEL%
rmdir "%~dp0.scheduler.lock" 2>nul
if %SCHED_EXIT% neq 0 (
    echo.
    echo scheduled_ingest.py exited with an error ^(see above^).
    pause
)
