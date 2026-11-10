"""Reset only the explicitly configured disposable FulfillOps test database."""

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text


ALLOWED_ENVIRONMENTS = {"test", "development"}
REQUIRED_DATABASE_MARKER = "fulfillops"


def main() -> int:
    environment = os.getenv("APP_ENV", "").lower()
    database_url = os.getenv("DATABASE_URL", "")
    if environment not in ALLOWED_ENVIRONMENTS:
        print("Refusing reset: APP_ENV must be test or development.", file=sys.stderr)
        return 2
    if REQUIRED_DATABASE_MARKER not in database_url.lower():
        print("Refusing reset: DATABASE_URL must identify the FulfillOps database.", file=sys.stderr)
        return 2
    if any(value in database_url.lower() for value in ("prod", "production", "live")):
        print("Refusing reset: production-like database name detected.", file=sys.stderr)
        return 2

    project_root = Path(__file__).resolve().parents[1]
    migration = (project_root / "db" / "migrations" / "001_initial_schema.sql").read_text(
        encoding="utf-8"
    )
    seed = (project_root / "db" / "seed.sql").read_text(encoding="utf-8")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "DROP TABLE IF EXISTS work_items, idempotency_keys, order_items, "
                "orders, inventory, products CASCADE"
            )
        )
        for statement in migration.split(";"):
            if statement.strip():
                connection.execute(text(statement))
        for statement in seed.split(";"):
            if statement.strip():
                connection.execute(text(statement))
    print("Disposable FulfillOps test database reset successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
