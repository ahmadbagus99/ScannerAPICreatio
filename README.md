# Creatio API Scanner

Creatio API Scanner scans local Creatio C# packages, discovers service
endpoints, generates an OpenAPI document, provides a local Swagger UI preview,
and securely publishes documentation to a separate Viewer service.

The Scanner is designed to run close to the Creatio source code. It can use
local JSON files or PostgreSQL for persistent storage.

## Requirements

- Windows PowerShell 5.1 or PowerShell 7
- Python 3.10 or newer
- Read access to the Creatio C# source directory
- PostgreSQL only when the `postgres` storage backend is selected

## Quick Start

Open PowerShell in the Scanner repository and run the interactive setup:

```powershell
.\setup.ps1
```

Select one of the storage backends:

```text
1. JSON files
2. PostgreSQL
```

Then start the Scanner:

```powershell
.\start.ps1
```

Open:

```text
http://127.0.0.1:8080
```

Press `Ctrl+C` to stop the service.

If `start.ps1` is executed before setup, it automatically starts the
interactive setup.

## Storage Setup

### JSON

JSON is the default and does not require a database:

```powershell
.\setup.ps1 -StorageBackend json
.\start.ps1
```

Scanner data is stored under:

```text
data/catalog.json
data/settings.json
data/instances/{slug}/openapi.json
data/instances/{slug}/scan-result.json
generated/
```

### PostgreSQL

Run:

```powershell
.\setup.ps1 `
  -StorageBackend postgres `
  -DatabaseUrl "postgresql://user:password@localhost:5432/creatio_scanner"
```

The setup script creates `.venv`, installs the PostgreSQL driver from
`requirements.txt`, and saves the local configuration.

Start the service:

```powershell
.\start.ps1
```

The following tables are created automatically:

```text
scanner_instances
scanner_settings
scanner_documents
```

## Setup Options

The setup script supports:

```powershell
.\setup.ps1 `
  -StorageBackend json `
  -HostAddress "127.0.0.1" `
  -Port 8080
```

Available parameters:

| Parameter | Description | Default |
| --- | --- | --- |
| `StorageBackend` | `json` or `postgres` | Interactive selection |
| `DatabaseUrl` | PostgreSQL connection URL | Required for PostgreSQL |
| `HostAddress` | HTTP bind address | `127.0.0.1` |
| `Port` | Scanner HTTP port | `8080` |
| `SkipInstall` | Skip Python dependency installation | Disabled |

The generated configuration is stored in:

```text
.runtime/config.json
```

This file may contain a database password and is excluded from Git.
`start.ps1` reads it and sets `STORAGE_BACKEND`, `DATABASE_URL`, `HOST`, and
`PORT` for the Python process.

## Configure the Viewer Connection

Start the Scanner and open:

```text
http://127.0.0.1:8080/settings.html
```

Enter:

- The Viewer service URL, for example `http://127.0.0.1:8090`
- A descriptive Scanner name

Select **Register Scanner**. The Scanner generates a local installation ID and
a random Bearer token. The Viewer stores only the token hash.

An administrator must then approve the registration in the Viewer:

```text
http://127.0.0.1:8090/login.html
```

After approval, return to Scanner Settings and select **Check Status**.

## Scan and Publish

1. Open the Scanner dashboard.
2. Add or edit a Creatio instance.
3. Set the Creatio source directory and scanning options.
4. Run the scan.
5. Review the generated OpenAPI document.
6. Select **Publish** to send it to the Viewer.

Publishing uses the Scanner installation Bearer token. Publisher usernames and
passwords are not used.

The Scanner synchronizes Viewer publication status when the dashboard opens,
every ten minutes while active, and when **Sync with Viewer** is selected.

## Environment Variable Reference

The normal local workflow uses `setup.ps1` and `start.ps1`; manual environment
variables are not required. Deployment platforms may set these variables
directly:

```env
HOST=0.0.0.0
PORT=8080
STORAGE_BACKEND=postgres
DATABASE_URL=postgresql://user:password@localhost:5432/creatio_scanner
```

See `.env.example` for a template. The Python server does not automatically
load `.env` files.

## Repository Safety

The repository `.gitignore` excludes:

- `.runtime/` local configuration
- `.venv/` Python virtual environments
- `.env` files and secrets
- Python caches and test artifacts
- Runtime log files
- Common editor and operating-system files

