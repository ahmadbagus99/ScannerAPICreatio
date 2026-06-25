[CmdletBinding()]
param(
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$EnvironmentPath = Join-Path $Root ".env.docker"
$EnvironmentExamplePath = Join-Path $Root ".env.docker.example"
$ComposePath = Join-Path $Root "compose.yaml"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker tidak ditemukan. Install Docker Desktop atau Docker Engine terlebih dahulu."
}

docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine tidak aktif."
}

if (-not (Test-Path $EnvironmentPath)) {
    Copy-Item $EnvironmentExamplePath $EnvironmentPath
    Write-Host ""
    Write-Host "File .env.docker sudah dibuat."
    Write-Host "Ubah database password di:"
    Write-Host "  $EnvironmentPath"
    Write-Host ""
    throw "Konfigurasi deployment belum siap. Edit .env.docker lalu jalankan script ini lagi."
}

# Auto-detect HOST_BROWSE_ROOT jika belum di-set di .env.docker
$envContent = Get-Content $EnvironmentPath -Raw
if ($envContent -notmatch '(?m)^HOST_BROWSE_ROOT\s*=\s*.+') {
    if ($IsWindows) {
        $userHome = $env:USERPROFILE -replace '\\', '/'
        $drive = ($userHome -split '/')[0]
        $defaultBrowseRoot = "$drive/Users"
    } elseif ($IsMacOS) {
        $defaultBrowseRoot = "/Users"
    } else {
        $defaultBrowseRoot = "/home"
    }
    $env:HOST_BROWSE_ROOT = $defaultBrowseRoot
    Write-Host "HOST_BROWSE_ROOT tidak di-set, menggunakan default: $defaultBrowseRoot"
}

$preferredVersions = @("16-alpine", "15-alpine", "14-alpine", "17-alpine")
$postgresImage = $null

$localImages = docker images --format "{{.Repository}}:{{.Tag}}" 2>$null | Where-Object { $_ -like "postgres:*" }
if ($localImages) {
    foreach ($ver in $preferredVersions) {
        if ($localImages -contains "postgres:$ver") {
            $postgresImage = "postgres:$ver"
            Write-Host "Menggunakan image postgres lokal yang sudah ada: $postgresImage"
            break
        }
    }
    if (-not $postgresImage) {
        $postgresImage = ($localImages | Select-Object -First 1)
        Write-Host "Menggunakan image postgres lokal yang ditemukan: $postgresImage"
    }
} else {
    $postgresImage = "postgres:17-alpine"
    Write-Host "Tidak ada image postgres lokal. Akan pull: $postgresImage"
}

$env:POSTGRES_IMAGE = $postgresImage

$arguments = @(
    "compose",
    "--env-file", $EnvironmentPath,
    "-f", $ComposePath,
    "up",
    "--build"
)
if ($Detached) {
    $arguments += "-d"
}

& docker @arguments
exit $LASTEXITCODE

