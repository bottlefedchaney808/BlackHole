cd /d C:\Users\bottl\FinancialDevelopment

REM 1. Confirm the diagnosis
.venv\Scripts\python.exe -m pip show slowapi
REM expect: "WARNING: Package(s) not found: slowapi"

REM 2. Fix it
.venv\Scripts\python.exe -m pip install -r requirements.txt
REM expect: "...Successfully installed slowapi-0.1.9 limits-..." somewhere in the output

REM 3. Confirm it took
.venv\Scripts\python.exe -m pip show slowapi
REM expect: Name/Version/Location printed, no warning

REM 4. Start it and check the browser tab loads the dashboard
dashboard.bat
REM expect: "Uvicorn running on http://127.0.0.1:8787" — no traceback

REM 5. (separate terminal, while it's running) confirm the port is listening
netstat -ano | findstr /C:"127.0.0.1:8787" | findstr "LISTENING"

REM 6. Ctrl+C to stop, then re-run netstat to confirm nothing's still holding the port