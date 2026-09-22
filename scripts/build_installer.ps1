param([string]$Version = "0.4.0")
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
& "$PSScriptRoot\build_exe.ps1"
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { $iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "未安装 Inno Setup 6" }
& $iscc /DMyAppVersion=$Version installer\PianoConvert.iss
Write-Host "Installer: $(Resolve-Path installer\output\PianoConvertSetup-$Version.exe)"
