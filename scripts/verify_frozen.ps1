param(
    [string]$Exe = ""
)
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
if (-not $Exe) {
    $Exe = Join-Path (Get-Location) "dist\PianoConvert\PianoConvert.exe"
}
if (-not (Test-Path $Exe)) {
    throw "找不到打包结果: $Exe"
}
$env:QT_QPA_PLATFORM = "offscreen"
$p = Start-Process -FilePath $Exe -ArgumentList "--smoke" -Wait -PassThru -NoNewWindow
if ($p.ExitCode -ne 0) {
    $log = Join-Path $env:TEMP "PianoConvert-smoke.log"
    if (Test-Path $log) { Get-Content $log -Encoding UTF8 }
    throw "frozen smoke failed, exit $($p.ExitCode)"
}
Write-Host "frozen smoke OK"
