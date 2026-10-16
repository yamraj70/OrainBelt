@echo off
cd /d "%~dp0.."
echo Optional AI inference. Requires a compatible llama-cpp-python binary wheel.
echo On Windows, verify a compatible Python 3.11 64-bit CPU wheel is available.
.venv\Scripts\python.exe -m pip install --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu llama-cpp-python
if errorlevel 1 echo No compatible wheel was found. Keep model.enabled=false and features.local_model=false in YAML.
pause
