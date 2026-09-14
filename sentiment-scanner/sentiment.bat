@echo off
setlocal enabledelayedexpansion
REM Sentiment Scanner launcher — clean, scanner-loop-only.
REM
REM Starts sentiment-scanner/main.py in its natural loop mode using the
REM repo-root shared .venv (shared across all suites).  Strips inherited
REM PYTHONPATH / PYTHONHOME / VIRTUAL_ENV so the scanner's numpy loads.
REM
REM Keeps the PO-token server start (needed for YouTube caption transcripts)
REM and a quiet dependency check — both are scanner prerequisites, not extra
REM tooling.  All prompts (sector rotation, PDF report) are skipped so the
REM loop runs headless without hanging on input().
cd /d "%~dp0"

REM --- Strip Hermes venv pollution ---
set PYTHONPATH=
set PYTHONHOME=
set VIRTUAL_ENV=

echo ============================================================
echo  Sentiment Scanner
echo ============================================================

set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Root .venv not found -- from the repo root run:
    echo   py -3.12 -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

REM --- 1. Quiet dependency check (no-op when already satisfied) ---
pushd .. & "%PY%" -m pip install -q -r requirements.txt 2>nul & popd
if errorlevel 1 (
    echo WARNING: pip install reported an error; continuing anyway.
)

REM --- 2. PO-token server (YouTube captions) ---
set "POT_SERVER_URL=http://127.0.0.1:4416/ping"
set "POT_SERVER_DIR=C:\Users\bottl\bgutil-ytdlp-pot-provider\server"

curl.exe --max-time 2 --silent --output NUL --fail "%POT_SERVER_URL%" 2>nul
if not errorlevel 1 (
    echo PO-token server: running.
    goto :run
)

if not exist "%POT_SERVER_DIR%\build\main.js" (
    echo PO-token server: not found ^(YouTube captions will be skipped^).
    goto :run
)

echo Starting PO-token server...
start "bgutil-pot-server" /MIN cmd /c "cd /d %POT_SERVER_DIR% && node build\main.js"
set "POT_UP=0"
for /L %%i in (1,1,5) do (
    if "!POT_UP!"=="0" (
        timeout /t 1 /nobreak >nul
        curl.exe --max-time 2 --silent --output NUL --fail "%POT_SERVER_URL%" 2>nul
        if not errorlevel 1 set "POT_UP=1"
    )
)
if "!POT_UP!"=="1" (
    echo PO-token server: started.
) else (
    echo PO-token server: could not start ^(YouTube captions will be skipped^).
)

:run
echo.
echo Launching scanner...
echo.
"%PY%" main.py --skip-sector-prompt --skip-report-prompt %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo Scanner exited with an error ^(see above^).
    pause
)
endlocal
