[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ConfigPath = Join-Path $Root ".runtime\config.json"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $ConfigPath)) {
    Write-Host "Konfigurasi Scanner belum ada. Menjalankan setup..."
    & (Join-Path $Root "setup.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
if ($config.storageBackend -notin @("json", "postgres")) {
    throw "storageBackend harus bernilai 'json' atau 'postgres'."
}
if ($config.storageBackend -eq "postgres" -and [string]::IsNullOrWhiteSpace($config.databaseUrl)) {
    throw "databaseUrl wajib diisi untuk backend PostgreSQL."
}

if (Test-Path $VenvPython) {
    $Python = $VenvPython
}
else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) { throw "Python tidak ditemukan." }
    $Python = $PythonCommand.Source
}

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

