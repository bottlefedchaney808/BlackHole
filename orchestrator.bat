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
    set /p ORCH_TICKER="Ticker (blank to cancel): "
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
