$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$env:DOTNET_CLI_HOME = Join-Path $Root 'tools/vocals/dotnet-home'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = 'false'
$env:DOTNET_ADD_GLOBAL_TOOLS_TO_PATH = 'false'
$env:APPDATA = Join-Path $Root 'tools/vocals/dotnet-home/appdata'
$env:NUGET_PACKAGES = Join-Path $Root 'tools/vocals/dotnet-home/packages'
New-Item -ItemType Directory -Path $env:APPDATA -Force | Out-Null
$Dotnet = Join-Path $Root 'tools/vocals/dotnet/dotnet.exe'
if (-not (Test-Path -LiteralPath $Dotnet)) { throw 'Run scripts/setup_vocals.ps1 first; the project SDK is missing.' }
$Project = Join-Path $Root 'backend/vocal-worker/NeoNote.VocalWorker.csproj'
& $Dotnet restore $Project --configfile (Join-Path $Root 'backend/vocal-worker/NuGet.Config') -p:NuGetAudit=false
if ($LASTEXITCODE -ne 0) { throw 'Vocal worker offline restore failed.' }
& $Dotnet build $Project --no-restore --configuration Release --output (Join-Path $Root 'tools/vocals/worker')
if ($LASTEXITCODE -ne 0) { throw 'Vocal worker build failed.' }
Copy-Item -LiteralPath (Join-Path $Root 'tools/vocals/source/OpenUtau-0.1.573-beta/LICENSE.txt') -Destination (Join-Path $Root 'tools/vocals/worker/OpenUtau-LICENSE.txt')
$Probe = & $Dotnet (Join-Path $Root 'tools/vocals/worker/NeoNote.VocalWorker.dll') --probe
if ($LASTEXITCODE -ne 0) { throw 'Vocal worker probe failed.' }
$Probe | Set-Content -LiteralPath (Join-Path $Root 'tools/vocals/gpu-probe.json') -Encoding utf8
Write-Output $Probe
& conda run --no-capture-output -n piano python (Join-Path $Root 'scripts/write_vocal_resources.py')
if ($LASTEXITCODE -ne 0) { throw 'Vocal resource fingerprint failed.' }
