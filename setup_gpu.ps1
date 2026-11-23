$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$codexRoot = Join-Path $env:USERPROFILE 'Documents\Codex'
$pythonPath = 'C:\Python314\python.exe'
$pythonHome = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314'
$packageTarget = Join-Path $codexRoot 'cctv-video-gpu-packages'
$shortTemp = Join-Path $codexRoot 'cctv-gpu-tmp'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python 3.14 runtime not found at '$pythonPath'."
}
if (-not (Test-Path -LiteralPath (Join-Path $pythonHome 'Lib\encodings'))) {
    throw "Python 3.14 standard library not found under '$pythonHome'."
}
if (-not (Test-Path -LiteralPath $packageTarget)) {
    New-Item -ItemType Directory -Path $packageTarget | Out-Null
}
if (-not (Test-Path -LiteralPath $shortTemp)) {
    New-Item -ItemType Directory -Path $shortTemp | Out-Null
}

$env:PYTHONHOME = $pythonHome
$env:TEMP = $shortTemp
$env:TMP = $shortTemp
$pipArgs = @(
    '-m', 'pip', 'install', '--target', $packageTarget,
    '--upgrade',
    '-r', (Join-Path $projectRoot 'requirements.txt'),
    '-r', (Join-Path $projectRoot 'requirements-video.txt'),
    'torch==2.11.0+cu128', 'torchvision==0.26.0+cu128',
    '--index-url', 'https://download.pytorch.org/whl/cu128',
    '--extra-index-url', 'https://pypi.org/simple'
)
& $pythonPath @pipArgs
if ($LASTEXITCODE -ne 0) {
    throw 'GPU dependencies could not be installed.'
}

$env:PYTHONPATH = $packageTarget
& $pythonPath -c "import torch, torchvision; print({'torch':torch.__version__, 'torchvision':torchvision.__version__, 'cuda_build':torch.version.cuda, 'cuda_available':torch.cuda.is_available(), 'gpu':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None})"
if ($LASTEXITCODE -ne 0) {
    throw 'PyTorch GPU verification failed.'
}
