@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Project virtual environment was not found: .venv\Scripts\python.exe
    echo Create or repair the virtual environment before running this script.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" start.py
set EXIT_CODE=%ERRORLEVEL%

pause
exit /b %EXIT_CODE%
