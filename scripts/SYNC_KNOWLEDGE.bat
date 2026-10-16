@echo off
cd /d "%~dp0.."
.venv\Scripts\python.exe -m orion_db.cli sync
pause
