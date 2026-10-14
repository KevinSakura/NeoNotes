$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (!(Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Node.js and npm are required.' }
Set-Location (Join-Path $Root 'frontend\app')
& npm.cmd ci --cache ../../.cache/npm --no-fund --no-audit
exit $LASTEXITCODE
