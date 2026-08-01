@echo off
setlocal enabledelayedexpansion
REM Sentiment Scanner launcher.
REM   - Strips the Hermes venv from PATH/PYTHONPATH/PYTHONHOME so this
REM     project's OWN .venv (sentiment-scanner\.venv) loads correctly.
REM   - Uses sentiment-scanner\.venv, NOT the shared repo-root .venv used by
REM     dashboard.bat/tools.bat/orchestrator.bat -- this project's yt-dlp /
REM     bgutil-ytdlp-pot-provider / PO-token stack was installed here
REM     specifically (see requirements.txt and README.md).
REM   - Auto-installs any missing/updated dependencies before every run.
REM   - Auto-starts the bgutil PO-token server (for YouTube caption
REM     transcripts) if it isn't already running.
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=

echo ============================================================
echo  Sentiment Scanner
echo  - Using sentiment-scanner\.venv (project-local, not shared)
echo  - Checking dependencies (requirements.txt)...
echo  - Checking bgutil PO-token server (YouTube captions)...
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo sentiment-scanner\.venv not found -- run:
    echo   python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM --- 1. Make sure dependencies are up to date (quiet -- pip is fast when
REM     nothing needs installing, so this is a no-op most of the time). This
REM     is what prevents a missing package (e.g. the filelock
REM     ModuleNotFoundError that broke backfill.py before) from silently
REM     blocking main.py.
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo WARNING: pip install -r requirements.txt reported an error above.
    echo Continuing anyway -- main.py may fail if a required package is missing.
    echo.
)

REM --- 2. Make sure the bgutil PO-token server is reachable; start it if not.
set "POT_SERVER_URL=http://127.0.0.1:4416/ping"
set "POT_SERVER_DIR=C:\Users\bottl\bgutil-ytdlp-pot-provider\server"

curl.exe --max-time 2 --silent --output NUL --fail "%POT_SERVER_URL%"
if not errorlevel 1 (
    echo PO-token server already running.
    goto :run
)

if not exist "%POT_SERVER_DIR%\build\main.js" (
    echo Could not start PO-token server automatically -- YouTube captions will be skipped, see README.
    echo ^(Expected build at "%POT_SERVER_DIR%\build\main.js" -- not found.^)
    goto :run
)

echo PO-token server not reachable -- starting it in a new minimized window...
start "bgutil-pot-server" /MIN cmd /c "cd /d %POT_SERVER_DIR% && node build\main.js"

REM Give it a moment to come up, then retry the ping a few times.
set "POT_UP=0"
for /L %%i in (1,1,5) do (
    if "!POT_UP!"=="0" (
        timeout /t 1 /nobreak >nul
        curl.exe --max-time 2 --silent --output NUL --fail "%POT_SERVER_URL%"
        if not errorlevel 1 set "POT_UP=1"
    )
)

if "!POT_UP!"=="1" (
    echo Started PO-token server in a new window.
) else (
    echo Could not start PO-token server automatically -- YouTube captions will be skipped, see README.
)

:run
echo.
echo Launching main.py...
echo.
.venv\Scripts\python.exe main.py %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo main.py exited with an error ^(see above^).
    pause
)
endlocal
