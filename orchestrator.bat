@echo off
REM Grand orchestrator launcher — runs orchestrator.py on the shared .venv.
REM Examples:
REM   orchestrator.bat --unified --ticker NVDA --expiry 2026-10-16
REM   orchestrator.bat --suite options --ticker AAPL --target-years 0.25
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
if not exist ".venv\Scripts\python.exe" (
    echo Shared .venv not found at %~dp0.venv — run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
if "%~1"=="" (
    echo No arguments given — this script is normally run from a command prompt with flags:
    echo   orchestrator.bat --unified --ticker TICKER [--expiry YYYY-MM-DD ^| --target-years N] [--strike N] [--option-type call^|put]
    echo   orchestrator.bat --suite options^|vol^|var^|sentiment --ticker TICKER [...]
    echo.
    echo Or answer these prompts for a quick unified run:

    REM Check if PowerShell is available (required for TTY detection)
    powershell -NoProfile -Command "exit 0" 2>nul
    if %ERRORLEVEL% neq 0 (
        echo.
        echo ERROR: orchestrator.bat requires PowerShell for interactive mode detection.
        echo PowerShell is not available or not in PATH.
        echo.
        echo SOLUTIONS:
        echo   1. Install or enable PowerShell on this system
        echo   2. Use CLI arguments instead: orchestrator.bat --unified --ticker TICKER
        echo.
        pause
        exit /b 1
    )

    REM PowerShell is available — check if stdin is interactive
    for /f "tokens=*" %%A in ('powershell -NoProfile -Command "[console]::isInputRedirected()"') do (
        set INPUT_REDIRECTED=%%A
    )

    if "%INPUT_REDIRECTED%"=="True" (
        echo.
        echo ERROR: orchestrator.bat requires an interactive terminal for manual ticker entry.
        echo stdin is redirected or not available (piped input, task scheduler, SSH without TTY, etc.).
        echo.
        echo SOLUTIONS:
        echo   1. Run from cmd.exe or PowerShell prompt directly
        echo   2. Use CLI arguments instead: orchestrator.bat --unified --ticker TICKER [--expiry YYYY-MM-DD]
        echo.
        pause
        exit /b 1
    )

    REM Ensure prompt is visible (toggle ECHO ON around set /p)
    @echo on
    set /p ORCH_TICKER="Ticker (blank to cancel): "
    @echo off

    REM Trim leading/trailing whitespace from ticker input
    for /f "tokens=*" %%A in ("%ORCH_TICKER%") do set ORCH_TICKER=%%A

    REM Check if blank AFTER trimming (catches space-only input)
    if "%ORCH_TICKER%"=="" (
        echo Cancelled.
        pause
        exit /b 0
    )

    set /p ORCH_EXPIRY="Expiry YYYY-MM-DD (blank = 0.25yr default^): "
    if "%ORCH_EXPIRY%"=="" (
        .venv\Scripts\python.exe orchestrator.py --unified --ticker %ORCH_TICKER%
    ) else (
        .venv\Scripts\python.exe orchestrator.py --unified --ticker %ORCH_TICKER% --expiry %ORCH_EXPIRY%
    )
    echo.
    pause
    exit /b 0
)
.venv\Scripts\python.exe orchestrator.py %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo orchestrator.py exited with an error ^(see above^).
    pause
)
