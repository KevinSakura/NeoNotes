param([Parameter(Mandatory=$true)][string]$InputFile)
$ErrorActionPreference = 'Stop'
conda run --no-capture-output -n piano python (Join-Path $PSScriptRoot 'replay_practice_vectors.py') $InputFile
exit $LASTEXITCODE
