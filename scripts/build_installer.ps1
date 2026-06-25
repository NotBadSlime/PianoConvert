param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$ISCC = ""
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

& (Join-Path $Root "scripts\build_exe.ps1") -Python $Python

if (!$ISCC) {
    $Candidates = @(
        "C:\Users\12849\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) {
            $ISCC = $Candidate
            break
        }
    }
    if (!$ISCC) {
        $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($Command) {
            $ISCC = $Command.Source
        }
    }
}

if (!$ISCC -or !(Test-Path $ISCC)) {
    throw "Inno Setup compiler ISCC.exe was not found."
}

$ScriptPath = Join-Path $Root "packaging\PianoConvert.iss"
if (!(Test-Path $ScriptPath)) {
    throw "Inno Setup script not found: $ScriptPath"
}

& $ISCC $ScriptPath
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup build failed."
}

$Installers = Get-ChildItem (Join-Path $Root "installer") -Filter "PianoConvertSetup-*.exe" | Sort-Object LastWriteTime -Descending
if (!$Installers) {
    throw "Installer output missing."
}

Write-Host "Built installer: $($Installers[0].FullName)"
