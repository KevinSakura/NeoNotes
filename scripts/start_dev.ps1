param([ValidateSet('backend', 'frontend')][string]$Service = 'backend')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if ($Service -eq 'backend') {
    Set-Location $Root
    $env:PYTHONUTF8 = '1'
    conda run --no-capture-output -n piano python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
} else {
    Set-Location (Join-Path $Root 'frontend\app')
    & npm.cmd run dev
}
exit $LASTEXITCODE
