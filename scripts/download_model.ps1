param(
    [string]$Url = "",
    [string]$Output = "piano_transcription_inference_data\note_F1=0.9677_pedal_F1=0.9186.pth",
    [long]$MinimumBytes = 100MB
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (!$Url) {
    $Url = $env:MODEL_URL
}
if (!$Url) {
    $Url = "https://zenodo.org/record/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1"
}

if ([System.IO.Path]::IsPathRooted($Output)) {
    $OutputPath = $Output
} else {
    $OutputPath = Join-Path $Root $Output
}

$OutputDir = Split-Path $OutputPath
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

if ((Test-Path $OutputPath) -and ((Get-Item $OutputPath).Length -ge $MinimumBytes)) {
    Write-Host "Model checkpoint already exists: $OutputPath"
    exit 0
}

$LastError = $null
for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
    try {
        Write-Host "Downloading model checkpoint, attempt $Attempt of 3..."
        Invoke-WebRequest -Uri $Url -OutFile $OutputPath -UseBasicParsing
        $LastError = $null
        break
    } catch {
        $LastError = $_
        if ($Attempt -lt 3) {
            Start-Sleep -Seconds (5 * $Attempt)
        }
    }
}

if ($LastError) {
    throw $LastError
}

if (!(Test-Path $OutputPath)) {
    throw "Model checkpoint download failed: $OutputPath"
}

$Size = (Get-Item $OutputPath).Length
if ($Size -lt $MinimumBytes) {
    throw "Downloaded model checkpoint looks invalid: $OutputPath ($Size bytes)"
}

Write-Host "Model checkpoint ready: $OutputPath"
