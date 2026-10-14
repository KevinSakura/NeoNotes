$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (!(Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda not found. Run this script from a terminal with Conda available."
}

conda run -n piano python -c "import sys; print(sys.executable)"
if ($LASTEXITCODE -ne 0) { throw "Cannot use the Conda piano environment. Create or repair it first." }

conda run -n piano python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

conda run -n piano python -m pip check
exit $LASTEXITCODE
