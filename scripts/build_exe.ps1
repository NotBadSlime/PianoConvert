$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
.\.venv\Scripts\python.exe -m pip install "setuptools>=65,<81"
.\.venv\Scripts\python.exe -m pip install --ignore-requires-python --no-deps homr==0.7.0
.\.venv\Scripts\python.exe -m pip install musicxml==1.4 "opencv-python-headless>=4.10,<4.13" "pypdfium2>=4,<6" "rapidocr>=3.7,<4"
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller app.spec --noconfirm
& "$PSScriptRoot\verify_frozen.ps1"
