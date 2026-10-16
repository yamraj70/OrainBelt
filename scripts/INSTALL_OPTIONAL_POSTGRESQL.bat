@echo off
cd /d "%~dp0.."
.venv\Scripts\python.exe -m pip install -r requirements-postgresql.txt
pause
