param([string]$Device = 'auto')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$codexRoot = Join-Path $env:USERPROFILE 'Documents\Codex'
$pythonPath = 'C:\Python314\python.exe'
$pythonHome = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314'
$packageTarget = Join-Path $codexRoot 'cctv-video-gpu-packages'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python 3.14 runtime not found at '$pythonPath'."
}
if (-not (Test-Path -LiteralPath (Join-Path $packageTarget 'torch'))) {
    throw "GPU packages not found. Run './setup_gpu.ps1' first."
}

Set-Location -LiteralPath $projectRoot
$env:PYTHONHOME = $pythonHome
$env:PYTHONPATH = "$packageTarget;$projectRoot"
if (-not $env:APP_DATA_DIR) {
    $env:APP_DATA_DIR = Join-Path $projectRoot 'data'
}
$env:YOLO_DEVICE = $Device
& $pythonPath -m uvicorn app.main:app --host 0.0.0.0 --port 8000
if ($LASTEXITCODE -ne 0) {
    throw 'FastAPI exited with an error.'
}
