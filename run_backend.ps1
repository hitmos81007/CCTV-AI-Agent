$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

$pythonCommand = $null
$pythonArgs = @()
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    foreach ($minor in @(15, 14, 13, 12, 11)) {
        try {
            $selector = "-3.$minor"
            & py $selector --version *> $null
            if ($LASTEXITCODE -eq 0) {
                $pythonCommand = 'py'
                $pythonArgs = @($selector)
                break
            }
        } catch { }
    }
}
if (-not $pythonCommand) {
    $pythonCommand = (Get-Command python -ErrorAction Stop).Source
    $versionText = & $pythonCommand --version
    if ($versionText -notmatch 'Python 3\.(1[1-5])\.') {
        Write-Warning 'This Python can run the API core, but the current CUDA PyTorch video path requires Python 3.11–3.15 on Windows.'
    }
}

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $pythonCommand @pythonArgs -m venv .venv
}

$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}

& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install -r requirements.txt
& $pythonPath -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env
