import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://fulfillops:fulfillops@localhost:5432/fulfillops_test",
)


@pytest.fixture(autouse=True)
def reset_database():
    """Reset the disposable database before every test."""
    environment = os.environ.copy()
    environment["APP_ENV"] = "test"

    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "reset_test_data.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as test_client:
        yield test_client
