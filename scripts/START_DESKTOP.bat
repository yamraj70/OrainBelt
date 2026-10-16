@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (echo Run scripts\INSTALL_WINDOWS.bat first. & pause & exit /b 1)
.venv\Scripts\python.exe -m orion_db.cli desktop
pause
