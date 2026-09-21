$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller app.spec --noconfirm
