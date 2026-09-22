# 把 NVIDIA GPU 组件下载到 %LOCALAPPDATA%\PianoConvert\gpu-runtime
# 窗口里会逐行打印 PROGRESS、百分比和已下载大小。
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = Split-Path -Parent $here
$exe = Join-Path $appDir "PianoConvert.exe"
if (Test-Path $exe) {
    & $exe --install-gpu
    exit $LASTEXITCODE
}
$py = Join-Path $appDir ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "py"
}
& $py -m app.gpu_setup
exit $LASTEXITCODE
