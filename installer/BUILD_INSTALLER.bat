@echo off
cd /d "%~dp0"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (echo Install Inno Setup 6 from jrsoftware.org first. & pause & exit /b 1)
"%ISCC%" ORION_Database_Enterprise.iss
pause
