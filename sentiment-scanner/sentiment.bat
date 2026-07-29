@echo off
REM Sentiment Scanner launcher — strips Hermes venv from path so .venv loads correctly
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
..\.venv\Scripts\python.exe main.py %*
