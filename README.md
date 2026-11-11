# FulfillOps

Resilient asynchronous order-processing platform for the DevOps capstone.

## Local development setup

### Prerequisites

- Git
- Python 3.11 or newer
- Docker Desktop with Docker Compose v2
- PowerShell on Windows

### Windows PowerShell

Open PowerShell in the repository directory:

```powershell
cd "C:\Users\<your-user>\Desktop\Bsc\S5\DevOps Approach\final_project\FULFILLOPS"
```

Create the virtual environment:

```powershell
python -m venv .venv
```

Activate it:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Install development dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Verify the environment:

```powershell
python --version
python -m pip --version
```

The prompt should display `(.venv)` while the environment is active.
The `.venv` directory is local-only and must not be committed.

Deactivate the environment when finished:

```powershell
deactivate
```

### WSL Ubuntu

Use WSL consistently if running Docker, kind, kubectl and Bash commands
through Ubuntu:

```bash
sudo apt update
sudo apt install -y python3-venv
cd "/mnt/c/Users/<your-user>/Desktop/Bsc/DevOps Approach/final_project/FULFILLOPS"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Do not mix activation commands between environments:

- PowerShell: `.\.venv\Scripts\Activate.ps1`
- WSL/Linux: `source .venv/bin/activate`

## Local Compose stack

Start PostgreSQL, the API, and the durable worker:

```powershell
docker compose up -d --build
docker compose ps
```

Check API readiness:

```powershell
curl http://localhost:8080/health/ready
```

Stop the services without deleting the database volume:

```powershell
docker compose down
```

To destroy the disposable local database and recreate the required seed stock:

```powershell
docker compose down -v
docker compose up -d --build
```

The reset command is for the local test database only. Never run it against a
production database.

Reset the disposable database manually when needed:

```powershell
$env:APP_ENV = "test"
$env:DATABASE_URL = "postgresql+psycopg://fulfillops:fulfillops@localhost:5432/fulfillops"
python scripts/reset_test_data.py
```

Run the local tests:

```powershell
python -m pytest
```

## Development status

The application baseline is being implemented. See the supplied
`API_CONTRACT_EN.md` for the authoritative HTTP contract and
`docs/ADR-001.md` for the initial architecture decision.

## Planned services

- `api`: FastAPI HTTP service
- `worker`: durable asynchronous order processor
- `postgres`: persistent database
