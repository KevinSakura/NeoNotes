$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (!(Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda not found. Run this script from a terminal with Conda available."
}

conda run --no-capture-output -n piano python -c "import sys; print(sys.executable)"
if ($LASTEXITCODE -ne 0) { throw "Cannot use the Conda piano environment. Create or repair it first." }

if (!(Test-Path "backend\tests\test_workstation.py")) {
    throw "Backend business tests are missing."
}
$env:PYTHONUTF8 = "1"
conda run --no-capture-output -n piano python -m ruff check backend
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
conda run --no-capture-output -n piano python -m pytest backend\tests -q -p no:cacheprovider
exit $LASTEXITCODE
