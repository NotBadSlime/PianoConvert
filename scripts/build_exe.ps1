param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Resolve-CommandOrPath {
    param(
        [string]$Value,
        [string]$Description
    )

    if (Test-Path $Value) {
        return (Resolve-Path $Value).Path
    }

    $Command = Get-Command $Value -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    throw "$Description not found: $Value"
}

$ModelPath = Join-Path $Root "piano_transcription_inference_data\note_F1=0.9677_pedal_F1=0.9186.pth"
$SpecPath = Join-Path $Root "PianoConvert.spec"
$ExePath = Join-Path $Root "dist\PianoConvert\PianoConvert.exe"
$PythonExe = Resolve-CommandOrPath -Value $Python -Description "Python interpreter"

if (!(Test-Path $ModelPath)) {
    throw "Model checkpoint not found: $ModelPath"
}

if (!(Test-Path $SpecPath)) {
    throw "PyInstaller spec not found: $SpecPath"
}

& $PythonExe -c "import PyInstaller, PySide6, torch, librosa, torchlibrosa" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Build dependencies are not installed for $PythonExe. Run: $PythonExe -m pip install -r requirements.txt"
}

& $PythonExe -m PyInstaller --clean --noconfirm $SpecPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

if (!(Test-Path $ExePath)) {
    throw "PyInstaller output missing: $ExePath"
}

Write-Host "Built executable: $ExePath"
