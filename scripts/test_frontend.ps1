$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root "frontend\app"

if (!(Test-Path (Join-Path $Frontend "package.json"))) {
    throw "frontend/app/package.json is missing. The formal frontend cannot be checked."
}

Set-Location $Frontend
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& npm.cmd test
exit $LASTEXITCODE
