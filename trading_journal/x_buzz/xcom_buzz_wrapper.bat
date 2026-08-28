@echo off
REM ─── X.com Fintwit Buzz Wrapper v1.0 ────────────────────────────────────
REM Runs scanner → report → logs result. Always exits 0 so the schedule survives.
REM Usage: xcom_buzz_wrapper.bat --window morning|mid-day|power-hour [--asof ISO]
REM ─────────────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion

REM ── Paths ────────────────────────────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
set "SCANNER=%SCRIPT_DIR%xcom_buzz_scan.py"
set "REPORTER=%SCRIPT_DIR%xcom_buzz_report.py"
REM Strip the trailing backslash so quoted args like --out-dir "%OUT_DIR%" do not
REM become "C:\dir\" (cmd escapes the closing quote, corrupting the path).
set "OUT_DIR=%SCRIPT_DIR%"
if "%OUT_DIR:~-1%"=="\" set "OUT_DIR=%OUT_DIR:~0,-1%"
set "LOG_FILE=%OUT_DIR%\run.log"

REM ── Discord delivery (default sink for every run) ─────────────────────────
REM Set DELIVER_TO_DISCORD=0 in the environment to disable. Bare "discord"
REM target routes to the gateway's Discord home channel (configured under
REM the profile's hermes dir). HERMES_HOME must point at the profile hermes
REM home (personal-bot) that owns the Discord platform config.
if not defined HERMES_HOME set "HERMES_HOME=%LOCALAPPDATA%\hermes\profiles\personal-bot"
REM Use the absolute hermes launcher so delivery works under Task Scheduler's
REM minimal PATH (no reliance on `hermes` being on PATH).
if not defined HERMES_BIN set "HERMES_BIN=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe"
if "%DELIVER_TO_DISCORD%"=="" set "DELIVER_TO_DISCORD=1"

REM ── Load local .env (holds secrets such as TWITTER_BEARER_TOKEN) ──────────
REM If x_buzz/.env exists, export its KEY=VALUE pairs into this process so the
REM scanner subprocess inherits them. This is the persistent home for the X API
REM v2 bearer: drop `TWITTER_BEARER_TOKEN=...` into x_buzz/.env and every
REM scheduled run picks it up — no need to edit this file or the Task Scheduler
REM task's environment. Comment lines (starting with #) are skipped.
set "XBUZZ_ENV=%OUT_DIR%.env"
if exist "%XBUZZ_ENV%" (
    for /f "usebackq eol=# tokens=1* delims==" %%A in ("%XBUZZ_ENV%") do (
        if not "%%A"=="" (
            set "%%A=%%B"
        )
    )
)

REM ── Parse arguments ──────────────────────────────────────────────────────
set "WINDOW_ID="
set "ASOF_ARG="

:parse_args
if "%~1"=="" goto done_args
if /I "%~1"=="--window" (
    set "WINDOW_ID=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="--asof" (
    set "ASOF_ARG=--asof %~2"
    shift
    shift
    goto parse_args
)
shift
goto parse_args

:done_args

if "%WINDOW_ID%"=="" (
    echo [error] --window is required ^(morning, mid-day, power-hour^)
    exit /b 1
)

REM ── Validate window ID ───────────────────────────────────────────────────
if /I not "%WINDOW_ID%"=="morning" if /I not "%WINDOW_ID%"=="mid-day" if /I not "%WINDOW_ID%"=="power-hour" (
    echo [error] Invalid window ID: %WINDOW_ID%
    exit /b 1
)

REM ── Get current date in YYYY-MM-DD format ────────────────────────────────
for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set "TODAY=%%a-%%b-%%c"
REM Fallback: use PowerShell for reliable ISO date
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd'"') do set "TODAY=%%a"

REM ── Temp scan output ────────────────────────────────────────────────────
set "TEMP_SCAN=%TEMP%\xcom_buzz_%WINDOW_ID%_%RANDOM%.json"

echo [%date% %time%] Starting wrapper: window=%WINDOW_ID% >> "%LOG_FILE%"

REM ── Step 1: Run scanner ──────────────────────────────────────────────────
echo [scan] Running xcom_buzz_scan.py --window %WINDOW_ID% %ASOF_ARG% --out %TEMP_SCAN%
python "%SCANNER%" --window %WINDOW_ID% %ASOF_ARG% --out "%TEMP_SCAN%"
set "SCAN_EXIT=%errorlevel%"

if not %SCAN_EXIT%==0 (
    echo [%date% %time%] window=%WINDOW_ID% status=fail scan_exit=%SCAN_EXIT% >> "%LOG_FILE%"
    echo [error] Scanner exited with code %SCAN_EXIT%
    REM Clean up temp file
    if exist "%TEMP_SCAN%" del "%TEMP_SCAN%"
    REM Always exit 0 so Task Scheduler survives
    exit /b 0
)

REM ── Step 2: Run report renderer ──────────────────────────────────────────
echo [report] Running xcom_buzz_report.py --in %TEMP_SCAN% --window %WINDOW_ID% --out-dir %OUT_DIR%
python "%REPORTER%" --in "%TEMP_SCAN%" --window %WINDOW_ID% --out-dir "%OUT_DIR%"
set "REPORT_EXIT=%errorlevel%"

if not %REPORT_EXIT%==0 (
    echo [%date% %time%] window=%WINDOW_ID% status=fail report_exit=%REPORT_EXIT% >> "%LOG_FILE%"
    echo [error] Report renderer exited with code %REPORT_EXIT%
    if exist "%TEMP_SCAN%" del "%TEMP_SCAN%"
    exit /b 0
)

REM ── Step 3: Log success ──────────────────────────────────────────────────
echo [%date% %time%] window=%WINDOW_ID% status=ok out=%OUT_DIR%\%TODAY%_%WINDOW_ID%.md >> "%LOG_FILE%"
echo [ok] Done: %OUT_DIR%\%TODAY%_%WINDOW_ID%.md

REM ── Step 4: Deliver to Discord (default sink) ─────────────────────────────
if "%DELIVER_TO_DISCORD%"=="1" (
    echo [deliver] Sending %OUT_DIR%\%TODAY%_%WINDOW_ID%.md to Discord...
    %HERMES_BIN% send --to discord --subject "[x.com Fintwit Buzz - %TODAY% %WINDOW_ID%]" --file "%OUT_DIR%\%TODAY%_%WINDOW_ID%.md" >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] window=%WINDOW_ID% discord_deliver=fail >> "%LOG_FILE%"
    ) else (
        echo [%date% %time%] window=%WINDOW_ID% discord_deliver=ok >> "%LOG_FILE%"
    )
)

REM ── Cleanup ──────────────────────────────────────────────────────────────
if exist "%TEMP_SCAN%" del "%TEMP_SCAN%"

REM Always exit 0
exit /b 0
