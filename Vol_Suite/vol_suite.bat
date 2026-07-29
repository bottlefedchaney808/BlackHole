@echo off
REM Vol Suite launcher — strips Hermes venv from path so .venv loads correctly
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
..\.venv\Scripts\python.exe volatility_suite.py %*
