@echo off
cd /d "%~dp0.."
python -m pip install -r requirements-voice.txt
if errorlevel 1 exit /b 1
python scripts\DOWNLOAD_VOICE_MODEL.py
pause
