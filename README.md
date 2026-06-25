# Creatio API Scanner

Creatio API Scanner scans local Creatio C# packages, discovers service
endpoints, generates an OpenAPI document, provides a local Swagger UI preview,
and securely publishes documentation to a separate Viewer service.

The Scanner is designed to run close to the Creatio source code. It can use
local JSON files or PostgreSQL for persistent storage. A Docker Compose
deployment is also included.

## Requirements

- Windows PowerShell 5.1 or PowerShell 7
- Python 3.10 or newer
- Read access to the Creatio C# source directory
- PostgreSQL only when the `postgres` storage backend is selected

For container deployment:

- Docker Desktop or Docker Engine
- Docker Compose v2

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
3. Click the folder icon next to the **Creatio project path** field and browse
   to the Creatio source directory. The browser starts from the user home
   directory and works on native and Docker deployments alike.
4. Run the scan.
5. Review the generated OpenAPI document.
6. Select **Publish** to send it to the Viewer.

Publishing uses the Scanner installation Bearer token. Publisher usernames and
passwords are not used.

The Scanner synchronizes Viewer publication status when the dashboard opens,
every ten minutes while active, and when **Sync with Viewer** is selected.

## Docker Deployment

The Docker deployment:

1. Builds a Scanner image with Python and PowerShell (arm64 and amd64)
2. Uses an existing or shared PostgreSQL container, or starts a new one
3. Mounts the host user directory read-only so the folder browser can reach any Creatio source path
4. Runs `setup.ps1` and `start.ps1` inside the Scanner container

### First Run

Run:

```powershell
.\deploy-docker.ps1
```

The first run creates `.env.docker` from `.env.docker.example` and stops.
Edit the generated file:

```env
SCANNER_PORT=5002

# Folder root shown in the path browser — match your host OS
# Mac:     HOST_BROWSE_ROOT=/Users
# Linux:   HOST_BROWSE_ROOT=/home
# Windows: HOST_BROWSE_ROOT=C:/Users
HOST_BROWSE_ROOT=/Users

POSTGRES_DB=creatio_scanner
POSTGRES_USER=creatio_user
POSTGRES_PASSWORD=change-this-db-password
```

Deploy after saving:

```powershell
.\deploy-docker.ps1 -Detached
```

Open:

```text
http://localhost:5002
```

When creating a Scanner instance through the UI, use the folder browser to
navigate to the Creatio source directory. The browser shows host paths
(e.g. `/Users/you/Creatio/Pkg`) and the Scanner reads from the corresponding
mounted path inside the container automatically.

### Shared PostgreSQL

To reuse an existing PostgreSQL container instead of starting a new one, add
the connection details to `.env.docker`:

```env
POSTGRES_HOST=host.docker.internal
POSTGRES_PORT=5433
POSTGRES_DB=creatio_scanner
POSTGRES_USER=your_user
POSTGRES_PASSWORD=your_password
```

Create the database on the existing instance before deploying:

```sql
CREATE DATABASE creatio_scanner;
```

When `POSTGRES_HOST` is set the deploy script skips the managed database
container entirely.

### Viewer Connection from Docker

If the Viewer runs on the same Docker host, configure the Scanner Viewer URL as:

```text
http://host.docker.internal:5003
```

Do not use `127.0.0.1` for a host service from inside the Scanner container,
because it resolves back to the container itself.

### Docker Commands

View Scanner logs:

```powershell
docker compose --env-file .env.docker logs -f scanner
```

Stop the deployment:

```powershell
docker compose --env-file .env.docker down
```

Stop and delete runtime volumes:

```powershell
docker compose --env-file .env.docker down -v
```

## Environment Variable Reference

The normal local workflow uses `setup.ps1` and `start.ps1`. Manual environment
variables are only needed for headless or platform deployments.

### Native

`start.ps1` sets these automatically; override them only if needed:

| Variable | Description | Default |
| --- | --- | --- |
| `STORAGE_BACKEND` | `json` or `postgres` | From `.runtime/config.json` |
| `DATABASE_URL` | PostgreSQL connection URL | From `.runtime/config.json` |
| `HOST` | HTTP bind address | From `.runtime/config.json` |
| `PORT` | HTTP port | From `.runtime/config.json` |
| `BROWSE_ROOT` | Root folder for the path browser | Auto-detected by `start.ps1` |

`BROWSE_ROOT` defaults:

| OS | Default |
| --- | --- |
| Windows | `C:/Users` (drive detected from `USERPROFILE`) |
| macOS | `/Users` |
| Linux | `/home` |

### Docker

Set in `.env.docker`:

| Variable | Description | Default |
| --- | --- | --- |
| `SCANNER_PORT` | Host port for the Scanner UI | `5002` |
| `HOST_BROWSE_ROOT` | Host path shown in the folder browser | Required |
| `POSTGRES_DB` | Database name | Required |
| `POSTGRES_USER` | Database user | Required |
| `POSTGRES_PASSWORD` | Database password | Required |
| `POSTGRES_HOST` | External PostgreSQL host | Managed container |
| `POSTGRES_PORT` | External PostgreSQL port | `5432` |

See `.env.docker.example` for a full template.

## Repository Safety

The repository `.gitignore` excludes:

- `.runtime/` local configuration
- `.venv/` Python virtual environments
- `.env` files and secrets
- Scanner data and generated documents
- Python caches and test artifacts
- Runtime log files
- Common editor and operating-system files
