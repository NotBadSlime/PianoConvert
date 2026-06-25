@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Project virtual environment was not found: .venv\Scripts\python.exe
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m app
set EXIT_CODE=%ERRORLEVEL%

pause
exit /b %EXIT_CODE%
