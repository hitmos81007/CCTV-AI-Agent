param([string]$Device = 'auto')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

$pythonPath = Join-Path $env:USERPROFILE 'Documents\Codex\cctv-video-env\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Video environment not found at '$pythonPath'. Follow the Windows setup in README.md first."
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}

$env:APP_DATA_DIR = Join-Path $projectRoot 'data'
$env:YOLO_DEVICE = $Device
& $pythonPath -m pip install -r requirements.txt -r requirements-video.txt
if ($LASTEXITCODE -ne 0) {
    throw 'Could not install the backend/video requirements in cctv-video-env.'
}

& $pythonPath -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env
if ($LASTEXITCODE -ne 0) {
    throw 'FastAPI exited with an error.'
}
