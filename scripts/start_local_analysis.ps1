param(
    [Parameter(Mandatory=$true)][string]$ApiPython,
    [Parameter(Mandatory=$true)][string]$MlPython,
    [Parameter(Mandatory=$true)][string]$Config,
    [int]$Port = 8127,
    [string]$WebDist = ''
)
$ErrorActionPreference = 'Stop'
$cgRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $cgRoot
$cgConfig = (Resolve-Path -LiteralPath $Config).Path
$cgModels = Get-Content -LiteralPath $cgConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$env:PYTHONPATH = Join-Path $cgRoot 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:CG_DATA_DIR = Join-Path $cgRoot 'var/windows-local'
$env:CG_PIPELINE_MODE = 'full'
$env:CG_VISION_BACKEND = 'torch'
$env:CG_VLM_ENABLED = 'false'
$env:CG_PIPELINE_VERSION = 'windows-trained-personal-items-v1'
$env:CG_LOCAL_ANALYSIS_CONFIG = $cgConfig
$env:CG_LOCAL_MODEL_PYTHON = (Resolve-Path -LiteralPath $MlPython).Path
$env:CG_LOCAL_MODEL_WEIGHTS = $cgModels.weights.main.path
$env:YOLO_CONFIG_DIR = Join-Path $env:CG_DATA_DIR 'ultralytics'
$env:CG_HOST = '127.0.0.1'
$env:CG_PORT = [string]$Port
$env:CG_WEB_DIST = if ($WebDist) { $WebDist } else { Join-Path $cgRoot 'web/dist' }
$env:HF_HUB_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:YOLO_OFFLINE = '1'
$env:YOLO_AUTOINSTALL = 'false'
New-Item -ItemType Directory -Path $env:CG_DATA_DIR -Force | Out-Null
try {
    $cgInstanceLock = [System.IO.File]::Open(
        (Join-Path $env:CG_DATA_DIR 'local-server.lock'),
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
} catch [System.IO.IOException] {
    Write-Output "Сервис этого worktree уже запущен: http://127.0.0.1:$Port"
    exit 0
}
try {
    & $ApiPython -m cashguard.cli up --profile prod --port $Port
    $cgExitCode = $LASTEXITCODE
} finally {
    $cgInstanceLock.Dispose()
}
exit $cgExitCode
