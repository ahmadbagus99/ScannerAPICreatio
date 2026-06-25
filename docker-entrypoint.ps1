$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

$requiredVariables = @(
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD"
)

foreach ($name in $requiredVariables) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Environment variable $name wajib diisi."
    }
}

$databaseUser = [Uri]::EscapeDataString($env:POSTGRES_USER)
$databasePassword = [Uri]::EscapeDataString($env:POSTGRES_PASSWORD)
$databaseName = [Uri]::EscapeDataString($env:POSTGRES_DB)
$databaseHost = if ($env:POSTGRES_HOST) { $env:POSTGRES_HOST } else { "database" }
$databasePort = if ($env:POSTGRES_PORT) { $env:POSTGRES_PORT } else { "5432" }
$databaseUrl = "postgresql://${databaseUser}:${databasePassword}@${databaseHost}:${databasePort}/$databaseName"
$port = if ($env:PORT) { [int]$env:PORT } else { 8080 }

Write-Host "Menyiapkan Scanner..."
& (Join-Path $Root "setup.ps1") `
    -StorageBackend postgres `
    -DatabaseUrl $databaseUrl `
    -HostAddress "0.0.0.0" `
    -Port $port `
    -SkipInstall

Write-Host "Creatio source tersedia di /creatio (read-only)."
Write-Host "Gunakan path /creatio saat membuat instance Scanner."
Write-Host "Menjalankan Scanner..."
& (Join-Path $Root "start.ps1")
exit $LASTEXITCODE

