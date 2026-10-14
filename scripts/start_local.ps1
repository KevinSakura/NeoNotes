param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root 'frontend\app'
if (!(Get-Command conda -ErrorAction SilentlyContinue)) { throw 'Conda is required. Use a terminal where conda is available.' }
if (!(Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Node.js and npm are required.' }
Set-Location $Root
$env:PYTHONUTF8 = '1'
conda run --no-capture-output -n piano python -c "import fastapi, uvicorn, pydantic, pydantic_settings, httpx, mido, python_multipart; print('Python dependencies OK')"
if ($LASTEXITCODE -ne 0) { throw 'Install the Python requirements into conda piano before starting.' }
conda run --no-capture-output -n piano python scripts/check_audio.py
if ($LASTEXITCODE -ne 0) { Write-Warning 'Audio assets/tools are unavailable. Editing and MIDI remain available; audio operations will report errors.' }
conda run --no-capture-output -n piano python scripts/check_vocal_resources.py
if ($LASTEXITCODE -ne 0) { Write-Warning 'Local singer resources are unavailable. Vocal generation will show the reason; song editing and instrument audio remain available.' }
if (!(Test-Path (Join-Path $Frontend 'node_modules'))) { throw 'Run scripts/setup_frontend.ps1 first.' }
if (!$SkipBuild) {
    Push-Location $Frontend
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
if (!(Test-Path (Join-Path $Frontend 'dist\index.html'))) { throw 'The frontend has not been built.' }
Write-Host 'Open http://127.0.0.1:8000 . Ctrl+C stops the workstation.'
Write-Host 'Songs: data/<song-id>/ . Full lyrics compose once; instrumental full songs require an approved short song. Local Qixuan singing is on Save and Export.'
Write-Host 'Choose Practice for MIDI sheet reading and rhythm. Practice materials and answer records: data/.practice/ . Creation keyboard mapping is unchanged.'
Write-Host 'Only the backend loads MODEL_* from .env. Startup makes no paid requests; model configuration changes require a restart.'
conda run --no-capture-output -n piano python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
exit $LASTEXITCODE
