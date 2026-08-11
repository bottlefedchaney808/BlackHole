@echo off
REM Launches the local Quant Bridge (the Hermes desktop plugin's data +
REM control backend) on 127.0.0.1:8765. Loopback only.
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
if not exist ".venv\Scripts\python.exe" (
    echo Shared .venv not found at %~dp0.venv — run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Catch a stale/incomplete venv (packages missing vs. requirements.txt)
REM *before* uvicorn starts, so the failure is one clear line instead of a
REM Python traceback plus misleading "port already in use" advice.
.venv\Scripts\python.exe -c "import fastapi, uvicorn, pydantic" 2>nul
if errorlevel 1 (
    echo Your .venv is missing one or more required packages
    echo ^(fastapi / uvicorn / pydantic^). This usually means
    echo requirements.txt was updated after your venv was created.
    echo.
    echo Fix: .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Refuse to start a second bridge on the same port.
netstat -ano | findstr /C:"127.0.0.1:8765" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo A Quant Bridge already appears to be running on port 8765.
    echo Only run ONE instance of quant_bridge.bat at a time.
    pause
    exit /b 0
)
.venv\Scripts\python.exe -m uvicorn quant_bridge:app --host 127.0.0.1 --port 8765
if %ERRORLEVEL% neq 0 (
    echo.
    echo Quant Bridge exited with an error ^(see above^). If it says the port is
    echo already in use, another process already owns port 8765 — edit
    echo quant_bridge.bat and pick a different --port number.
    pause
)
