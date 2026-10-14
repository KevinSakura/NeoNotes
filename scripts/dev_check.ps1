$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

powershell -ExecutionPolicy Bypass -File scripts\test_backend.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
powershell -ExecutionPolicy Bypass -File scripts\test_frontend.ps1
exit $LASTEXITCODE
