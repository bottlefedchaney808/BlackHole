@echo off
cd /d "%~dp0"
set PYTHONPATH=
set VIRTUAL_ENV=
start "" "%~dp0.venv\Scripts\python.exe" -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791
timeout /t 2 >nul
start "" "http://127.0.0.1:8791/"
