[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ConfigPath = Join-Path $Root ".runtime\config.json"

function Find-Python {
    $candidates = @(
        (Join-Path (Join-Path (Join-Path $Root ".venv") "Scripts") "python.exe"),
        (Join-Path (Join-Path (Join-Path $Root ".venv") "bin") "python")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    foreach ($name in @("python", "python3")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }
    throw "Python tidak ditemukan."
}

if (-not (Test-Path $ConfigPath)) {
    Write-Host "Konfigurasi Scanner belum ada. Menjalankan setup..."
    & (Join-Path $Root "setup.ps1")
}

$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
if ($config.storageBackend -notin @("json", "postgres")) {
    throw "storageBackend harus bernilai 'json' atau 'postgres'."
}
if ($config.storageBackend -eq "postgres" -and [string]::IsNullOrWhiteSpace($config.databaseUrl)) {
    throw "databaseUrl wajib diisi untuk backend PostgreSQL."
}

$Python = Find-Python

$env:STORAGE_BACKEND = [string]$config.storageBackend
$env:HOST = [string]$config.host
$env:PORT = [string]$config.port
if ($config.databaseUrl) {
    $env:DATABASE_URL = [string]$config.databaseUrl
}
else {
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}

Write-Host "Menjalankan Scanner di http://$($config.host):$($config.port)"
& $Python -u (Join-Path $Root "server.py")
exit $LASTEXITCODE
