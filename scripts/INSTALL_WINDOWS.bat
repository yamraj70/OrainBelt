@echo off
setlocal
cd /d "%~dp0.."
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher not found. Install 64-bit Python 3.11 from python.org.
  pause
  exit /b 1
)
py -3.12 -m venv .venv
if errorlevel 1 (
  echo Python 3.12 unavailable. Install it, then run this script again.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
if not exist "data\demo_assets.sqlite" .venv\Scripts\python.exe -m orion_db.cli create-demo-db
.venv\Scripts\python.exe -m orion_db.cli sync
 echo.
 echo IMPORTANT: Create an employee account before signing in:
 echo   .venv\Scripts\python.exe -m orion_db.cli create-user admin --role admin
 echo.
pause
