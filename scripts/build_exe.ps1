$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
.\.venv\Scripts\python.exe -m pip install "setuptools>=65,<81"
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller app.spec --noconfirm
& "$PSScriptRoot\verify_frozen.ps1"
