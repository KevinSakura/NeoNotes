param([switch]$PrepareModels)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
Write-Host 'Installing optional CPU transcription dependencies in Conda piano (no .env).'
conda run --no-capture-output -n piano python scripts/check_transcription_install.py torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw 'PyTorch compatibility plan rejected; nothing installed.' }
conda run --no-capture-output -n piano python scripts/check_transcription_install.py -r requirements-transcription.txt
if ($LASTEXITCODE -ne 0) { throw 'Runtime compatibility plan rejected; nothing installed.' }
conda run --no-capture-output -n piano python -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw 'PyTorch installation failed.' }
conda run --no-capture-output -n piano python -m pip install -r requirements-transcription.txt
if ($LASTEXITCODE -ne 0) { throw 'Runtime installation failed.' }
conda run --no-capture-output -n piano python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency compatibility check failed.' }
if ($PrepareModels) {
    conda run --no-capture-output -n piano python scripts/prepare_transcription.py
    if ($LASTEXITCODE -ne 0) { throw 'Explicit model preparation failed.' }
} else {
    Write-Host 'Run conda run -n piano python scripts/prepare_transcription.py to download/verify official resources.'
}
