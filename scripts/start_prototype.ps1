$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Prototype = Join-Path $Root 'frontend\prototype'
if (!(Get-Command conda -ErrorAction SilentlyContinue)) {
    throw 'Conda not found. Use a terminal with Conda available.'
}
Write-Host 'Open http://127.0.0.1:8765 in your browser. Ctrl+C stops the preview.'
Write-Host 'Prototype only: no model API calls; no production backend; no data/ writes.'
conda run --no-capture-output -n piano python -m http.server 8765 --bind 127.0.0.1 --directory $Prototype
exit $LASTEXITCODE
