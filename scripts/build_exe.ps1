param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$ModelPath = Join-Path $Root "piano_transcription_inference_data\note_F1=0.9677_pedal_F1=0.9186.pth"
$SpecPath = Join-Path $Root "PianoConvert.spec"
$ExePath = Join-Path $Root "dist\PianoConvert\PianoConvert.exe"

if (!(Test-Path $Python)) {
    throw "Python interpreter not found: $Python"
}

if (!(Test-Path $ModelPath)) {
    throw "Model checkpoint not found: $ModelPath"
}

if (!(Test-Path $SpecPath)) {
    throw "PyInstaller spec not found: $SpecPath"
}

& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: $Python -m pip install -r requirements.txt"
}

& $Python -m PyInstaller --clean --noconfirm $SpecPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

if (!(Test-Path $ExePath)) {
    throw "PyInstaller output missing: $ExePath"
}

Write-Host "Built executable: $ExePath"
