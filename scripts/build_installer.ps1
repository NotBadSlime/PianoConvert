param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$ISCC = "",
    [string]$Version = $env:PIANOCONVERT_VERSION
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (!$Version) {
    $Version = "0.1.0"
}

& (Join-Path $Root "scripts\build_exe.ps1") -Python $Python

if (!$ISCC) {
    $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($Command) {
        $ISCC = $Command.Source
    }
}

if (!$ISCC) {
    $Candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) {
            $ISCC = $Candidate
            break
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

& $ISCC "/DMyAppVersion=$Version" $ScriptPath
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup build failed."
}

$InstallerPath = Join-Path $Root "installer\PianoConvertSetup-$Version.exe"
if (!(Test-Path $InstallerPath)) {
    throw "Installer output missing: $InstallerPath"
}

Write-Host "Built installer: $InstallerPath"
