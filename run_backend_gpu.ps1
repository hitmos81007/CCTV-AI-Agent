param(
    [string]$Device = 'auto',
    [ValidateSet('127.0.0.1', '0.0.0.0')]
    [string]$BindAddress = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$codexRoot = Join-Path $env:USERPROFILE 'Documents\Codex'
$pythonPath = 'C:\Python314\python.exe'
$pythonHome = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314'
$packageTarget = Join-Path $codexRoot 'cctv-video-gpu-packages'
$audioPackageTarget = Join-Path $codexRoot 'cctv-audio-packages'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python 3.14 runtime not found at '$pythonPath'."
}
if (-not (Test-Path -LiteralPath (Join-Path $packageTarget 'torch'))) {
    throw "GPU packages not found. Run './setup_gpu.ps1' first."
}

Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
$env:PYTHONHOME = $pythonHome
$env:PYTHONPATH = "$packageTarget;$projectRoot"
if (Test-Path -LiteralPath (Join-Path $audioPackageTarget 'faster_whisper')) {
    $env:PYTHONPATH = "$audioPackageTarget;$packageTarget;$projectRoot"
}
if (-not $env:APP_DATA_DIR) {
    $env:APP_DATA_DIR = Join-Path $projectRoot 'data'
}
if (-not $env:HF_HOME) {
    $env:HF_HOME = Join-Path $projectRoot 'data\model-cache'
}
$env:YOLO_DEVICE = $Device
& $pythonPath -m uvicorn app.main:app --host $BindAddress --port 8000 --env-file .env
if ($LASTEXITCODE -ne 0) {
    throw 'FastAPI exited with an error.'
}
