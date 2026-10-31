$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
.\.venv\Scripts\python.exe -m pip install "setuptools>=65,<81"
.\.venv\Scripts\python.exe -m pip install --ignore-requires-python --no-deps homr==0.7.0
.\.venv\Scripts\python.exe -m pip install --no-deps transkun==2.0.1
.\.venv\Scripts\python.exe -m pip install musicxml==1.4 "opencv-python-headless>=4.10,<4.13" "pypdfium2>=4,<6" "rapidocr>=3.7,<4"
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m pip install "torchvision==0.29.0" "Pillow>=10" "scipy>=1.11"
if ($LASTEXITCODE -ne 0) { throw "Copista dependencies install failed" }
.\.venv\Scripts\python.exe -m app.copista_models --package
if ($LASTEXITCODE -ne 0) { throw "Copista model preparation failed" }
# homr's models (about 134 MB) go beside the homr package so the installer ships them
.\.venv\Scripts\python.exe -m app.homr_models
if ($LASTEXITCODE -ne 0) { throw "homr models download failed" }
.\.venv\Scripts\python.exe -m PyInstaller app.spec --noconfirm
& "$PSScriptRoot\verify_frozen.ps1"
