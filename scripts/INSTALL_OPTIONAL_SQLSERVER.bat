@echo off
cd /d "%~dp0.."
echo Requires Microsoft ODBC Driver 18 for SQL Server installed separately.
.venv\Scripts\python.exe -m pip install -r requirements-sqlserver.txt
pause
