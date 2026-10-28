param([string]$Version = "1.0.0-beta.1")
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
& "$PSScriptRoot\build_exe.ps1"
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { $iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "未安装 Inno Setup 6" }
$numeric = [regex]::Match($Version, '^\d+(\.\d+){1,3}').Value
if (-not $numeric) { throw "无效版本: $Version" }
& $iscc /DMyAppVersion=$Version /DMyAppNumericVersion=$numeric installer\PianoConvert.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed: $LASTEXITCODE" }
Write-Host "Installer: $(Resolve-Path installer\output\PianoConvertSetup-$Version.exe)"
