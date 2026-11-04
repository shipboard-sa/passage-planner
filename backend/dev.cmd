@echo off
cd /d "%~dp0\.."
".venv\Scripts\python.exe" -m uvicorn backend.main:app --port 8100
