param([switch]$SkipSdk)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
$Destination = Join-Path $Root 'tools/vocals'
$Downloads = Join-Path $Destination 'downloads'
New-Item -ItemType Directory -Path $Downloads -Force | Out-Null
$Headers = @{ 'User-Agent' = 'NeoNote-local-vocal-setup' }
$Records = @()

function Get-OfficialFile($Url, $Target, $Digest, $Algorithm = 'SHA256') {
    if ((Test-Path -LiteralPath $Target) -and $Digest -and ((Get-FileHash -LiteralPath $Target -Algorithm $Algorithm).Hash.ToLower() -eq $Digest)) { return }
    Write-Host "Downloading official resource: $([IO.Path]::GetFileName($Target))"
    Invoke-WebRequest -Uri $Url -Headers $Headers -OutFile $Target -TimeoutSec 1800
    if ($Digest -and ((Get-FileHash -LiteralPath $Target -Algorithm $Algorithm).Hash.ToLower() -ne $Digest)) { throw "Checksum mismatch: $Target" }
}

foreach ($Spec in @(
    @{ Repo='yqzhishen/qixuan-diffsinger'; Tag='v2.7.0'; Asset='Qixuan_v2.7.0_DiffSinger_OpenUtau.zip'; Folder='singers' },
    @{ Repo='openvpi/vocoders'; Tag='pc-nsf-hifigan-44.1k-hop512-128bin-2025.02'; Asset='pc_nsf_hifigan_44.1k_hop512_128bin_2025.02.oudep'; Folder='Dependencies/pc_nsf_hifigan_44.1k_hop512_128bin_2025.02' },
    @{ Repo='openutau/OpenUtau'; Tag='0.1.573-beta'; Asset='OpenUtau-win-x64.zip'; Folder='openutau' }
)) {
    $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/$($Spec.Repo)/releases/tags/$($Spec.Tag)" -Headers $Headers -TimeoutSec 90
    $Asset = $Release.assets | Where-Object name -eq $Spec.Asset
    if (-not $Asset -or -not $Asset.digest.StartsWith('sha256:')) { throw 'Official asset or digest missing.' }
    $Archive = Join-Path $Downloads $Spec.Asset
    $Digest = $Asset.digest.Substring(7)
    Get-OfficialFile $Asset.browser_download_url $Archive $Digest
    $Folder = Join-Path $Destination $Spec.Folder
    if (-not (Test-Path -LiteralPath $Folder)) {
        Write-Host "Extracting $($Spec.Asset)"
        New-Item -ItemType Directory -Path $Folder -Force | Out-Null
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Folder)
    }
    $Records += @{ repo=$Spec.Repo; tag=$Spec.Tag; url=$Asset.browser_download_url; bytes=$Asset.size; sha256=$Digest; folder=$Spec.Folder }
}

Write-Host 'Downloading pinned OpenUtau source for API and license review.'
$SourceArchive = Join-Path $Downloads 'OpenUtau-0.1.573-beta-source.zip'
if (-not (Test-Path -LiteralPath $SourceArchive)) { Get-OfficialFile 'https://codeload.github.com/openutau/OpenUtau/zip/refs/tags/0.1.573-beta' $SourceArchive $null }
$SourceFolder = Join-Path $Destination 'source'
if (-not (Test-Path -LiteralPath $SourceFolder)) { [IO.Compression.ZipFile]::ExtractToDirectory($SourceArchive, $SourceFolder) }
$Records += @{ repo='openutau/OpenUtau'; tag='0.1.573-beta'; url='https://codeload.github.com/openutau/OpenUtau/zip/refs/tags/0.1.573-beta'; sha256=(Get-FileHash -LiteralPath $SourceArchive).Hash.ToLower(); folder='source' }

if (-not $SkipSdk) {
    $SdkVersion = '10.0.401'
    $Metadata = Invoke-RestMethod -Uri 'https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json' -TimeoutSec 90
    $Sdk = @($Metadata.releases | ForEach-Object { $_.sdks } | Where-Object version -eq $SdkVersion)[0]
    $SdkAsset = $Sdk.files | Where-Object { $_.rid -eq 'win-x64' -and $_.name -like '*.zip' }
    if (-not $SdkAsset) { throw 'Pinned official .NET SDK not found.' }
    $SdkArchive = Join-Path $Downloads "dotnet-sdk-$SdkVersion-win-x64.zip"
    Get-OfficialFile $SdkAsset.url $SdkArchive $SdkAsset.hash.ToLower() 'SHA512'
    $SdkFolder = Join-Path $Destination 'dotnet'
    if (-not (Test-Path -LiteralPath $SdkFolder)) { [IO.Compression.ZipFile]::ExtractToDirectory($SdkArchive, $SdkFolder) }
    $Records += @{ name='.NET SDK'; version=$SdkVersion; url=$SdkAsset.url; sha512=$SdkAsset.hash.ToLower(); folder='dotnet' }
}
$Records | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Destination 'official-resources.json') -Encoding utf8
Write-Host 'Official resources downloaded and checksums verified. Python piano was not changed.'
