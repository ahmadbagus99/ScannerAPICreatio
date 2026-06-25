[CmdletBinding()]
param(
    [ValidateSet("json", "postgres")]
    [string]$StorageBackend,

    [string]$DatabaseUrl,

    [string]$HostAddress = "127.0.0.1",

    [ValidateRange(1, 65535)]
    [int]$Port = 8080,

    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$RuntimeDirectory = Join-Path $Root ".runtime"
$ConfigPath = Join-Path $RuntimeDirectory "config.json"
$VenvDirectory = Join-Path $Root ".venv"

function Get-VenvPython {
    $windowsPython = Join-Path (Join-Path $VenvDirectory "Scripts") "python.exe"
    if (Test-Path $windowsPython) {
        return $windowsPython
    }

    return Join-Path (Join-Path $VenvDirectory "bin") "python"
}

function Find-Python {
    foreach ($name in @("python", "python3")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }
    throw "Python tidak ditemukan. Install Python 3.10+ lalu jalankan setup.ps1 lagi."
}

if (-not $StorageBackend) {
    Write-Host ""
    Write-Host "Pilih storage Scanner:"
    Write-Host "  1. JSON files (tanpa database)"
    Write-Host "  2. PostgreSQL"
    $choice = Read-Host "Pilihan [1]"

    if ([string]::IsNullOrWhiteSpace($choice) -or $choice -eq "1") {
        $StorageBackend = "json"
    }
    elseif ($choice -eq "2") {
        $StorageBackend = "postgres"
    }
    else {
        throw "Pilihan storage tidak valid."
    }
}

if ($StorageBackend -eq "postgres" -and [string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    $DatabaseUrl = Read-Host "PostgreSQL URL"
}
if ($StorageBackend -eq "postgres" -and [string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    throw "DatabaseUrl wajib diisi untuk backend PostgreSQL."
}

New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null
[ordered]@{
    storageBackend = $StorageBackend
    databaseUrl = if ($StorageBackend -eq "postgres") { $DatabaseUrl } else { $null }
    host = $HostAddress
    port = $Port
} | ConvertTo-Json | Set-Content -Path $ConfigPath -Encoding UTF8

if ($StorageBackend -eq "postgres" -and -not $SkipInstall) {
    $Python = Find-Python
    $VenvPython = Get-VenvPython
    if (-not (Test-Path $VenvPython)) {
        Write-Host "Membuat virtual environment Scanner..."
        & $Python -m venv $VenvDirectory
        if ($LASTEXITCODE -ne 0) { throw "Gagal membuat virtual environment." }
        $VenvPython = Get-VenvPython
    }

    Write-Host "Meng-install dependency Scanner..."
    & $VenvPython -m pip install -r (Join-Path $Root "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Gagal meng-install dependency." }
}

Write-Host ""
Write-Host "Setup Scanner selesai."
Write-Host "Storage : $StorageBackend"
Write-Host "URL     : http://${HostAddress}:$Port"
Write-Host "Jalankan: .\start.ps1"
