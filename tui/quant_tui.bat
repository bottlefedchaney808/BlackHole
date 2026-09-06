@echo off
rem Quant TUI — terminal client for the widget API. Ensures the dashboard is
rem up on 127.0.0.1:8787, then opens the TUI in a new Windows Terminal tab.
curl -s -o nul -w "%%{http_code}" --max-time 3 http://127.0.0.1:8787/api/widgets/catalog | findstr 200 >nul
if errorlevel 1 (
  echo Dashboard not responding on 8787 - starting it ...
  start "FinDev Dashboard" /D C:\Users\bottl\FinancialDevelopment cmd /k dashboard.bat
  timeout /t 10 /nobreak >nul
)
wt -w 0 new-tab --title "Quant TUI" -d "C:\Users\bottl\FinancialDevelopment" cmd /k .venv\Scripts\python.exe tui\quant_tui.py
