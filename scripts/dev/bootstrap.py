"""Bootstrap the local development database.

Waits for the compose Postgres, migrates to head, and seeds:
an anonymized production snapshot (``seeds/dev-snapshot.sql``) when one
exists, otherwise the synthetic seed. Always ensures the dev admin
account exists. Idempotent — re-running against a seeded database only
re-applies migrations and the admin check.

Run through ``kv seed``; direct invocation:

    uv run python scripts/dev/bootstrap.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
SNAPSHOT_PATH = REPO_ROOT / "seeds" / "dev-snapshot.sql"
DEV_DATABASE_URL = os.environ.get(
    "DEV_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:55432/kvarteret_personal_dev",
)
COMPOSE = ["docker", "compose", "-f", str(REPO_ROOT / "docker-compose.dev.yml")]


async def wait_for_database(timeout_seconds: int = 30) -> None:
    engine = create_async_engine(DEV_DATABASE_URL)
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                return
            except Exception:
                if time.monotonic() > deadline:
                    raise SystemExit(
                        "Could not reach the dev database on localhost:55432.\n"
                        "Start it with: kv start"
                    )
                await asyncio.sleep(1)
    finally:
        await engine.dispose()


def migrate() -> None:
    env = {**os.environ, "DATABASE_URL": DEV_DATABASE_URL}
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


async def is_seeded() -> bool:
    from app.db.tables import volunteer_records  # noqa: PLC0415 — after env is set

    engine = create_async_engine(DEV_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(select(volunteer_records.c.id).limit(1))
            return row.first() is not None
    finally:
        await engine.dispose()


def load_snapshot() -> None:
    print(f"Loading anonymized snapshot {SNAPSHOT_PATH.relative_to(REPO_ROOT)} …")
    with SNAPSHOT_PATH.open("rb") as handle:
        subprocess.run(
            [
                *COMPOSE,
                "exec",
                "-T",
                "db",
                "psql",
                "-q",
                "-U",
                "postgres",
                "-d",
                "kvarteret_personal_dev",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            stdin=handle,
            check=True,
        )


async def main() -> None:
    await wait_for_database()
    migrate()

    from seed_shifts import seed_shifts  # noqa: PLC0415
    from seed_synthetic import ensure_dev_admin, seed  # noqa: PLC0415

    if await is_seeded():
        print("Database already seeded — applying migrations and admin check only.")
    elif SNAPSHOT_PATH.exists():
        load_snapshot()
    else:
        print("No seeds/dev-snapshot.sql found — using the synthetic seed.")
        await seed(DEV_DATABASE_URL)

    if not SNAPSHOT_PATH.exists():
        # Skiftlista test data; skipped for real (anonymized) snapshots so
        # fake subgroups never mix with real group structure.
        await seed_shifts(DEV_DATABASE_URL)

    from app.config import Settings  # noqa: PLC0415
    from app.db.session import build_database_runtime  # noqa: PLC0415

    runtime = build_database_runtime(
        Settings(database_url=DEV_DATABASE_URL, app_env="development")
    )
    try:
        await ensure_dev_admin(runtime)
    finally:
        await runtime.aclose()

    print(
        "\nDev database ready.\n"
        f"  DATABASE_URL={DEV_DATABASE_URL}\n"
        "  Start the app:   kv start\n"
        "  Admin login:     dev@kvarteret.dev / dev-password "
        "(override via DEV_ADMIN_EMAIL / DEV_ADMIN_PASSWORD)\n"
        "  Dev emails land in .devdata/outbox/, photos in .devdata/photos/."
    )


if __name__ == "__main__":
    asyncio.run(main())
