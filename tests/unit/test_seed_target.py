"""The two seed checks: where the script points, and who is already in the database."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import BigInteger, Integer, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.metadata import public_metadata
from app.db.tables import user_accounts, volunteer_records
from scripts.dev.seed_target import (
    SeedRefused,
    check_database_target,
    ensure_only_fake_people,
    target_fingerprint,
)

POOLER = "aws-0-eu-west-1.pooler.supabase.com"


def _url(user: str, database: str = "postgres") -> str:
    return f"postgresql+asyncpg://{user}:pw@{POOLER}:6543/{database}"


def test_the_fingerprint_has_no_password_and_tells_projects_apart() -> None:
    demo, production = _url("postgres.demoproject"), _url("postgres.realproject")
    assert target_fingerprint(demo) == f"postgres.demoproject@{POOLER}:6543/postgres"
    assert "pw" not in target_fingerprint(demo)
    assert target_fingerprint(demo) != target_fingerprint(production)


def test_a_confirmation_for_the_demo_project_does_not_unlock_production() -> None:
    """Supabase's pooler host is shared by every project in a region; the user name differs."""
    demo, production = _url("postgres.demoproject"), _url("postgres.realproject")
    confirm_demo = target_fingerprint(demo)
    assert check_database_target(demo, confirm_demo) == POOLER
    with pytest.raises(SeedRefused, match="DEMO_SEED_CONFIRM=postgres.realproject@"):
        check_database_target(production, confirm_demo)
    # Same project, other database name: also not the same target.
    with pytest.raises(SeedRefused):
        check_database_target(_url("postgres.demoproject", "other"), confirm_demo)


@pytest.fixture
async def session(monkeypatch):
    # SQLite has no schemas and no BigInteger primary keys that count up by themselves.
    for table in (volunteer_records, user_accounts):
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                monkeypatch.setattr(column, "type", column.type.with_variant(Integer, "sqlite"))
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", execution_options={"schema_translate_map": {"public": None}}
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: public_metadata.create_all(conn, tables=[volunteer_records, user_accounts])
        )
    async with async_sessionmaker(engine)() as db:
        yield db
    await engine.dispose()


async def _add_volunteer(db, email: str | None) -> None:
    await db.execute(
        insert(volunteer_records).values(
            first_name="Test",
            last_name="Person",
            email=email,
            gender="A",
            birth_date=date(1999, 1, 1),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )


async def _add_user(db, email: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    await db.execute(
        insert(user_accounts).values(
            auth_user_id=uuid4(),
            username=email,
            email=email,
            role="Admin",
            onboarding_status="complete",
            created_at=now,
            updated_at=now,
        )
    )


@pytest.mark.asyncio
async def test_an_empty_database_and_fake_people_are_fine(session) -> None:
    await ensure_only_fake_people(session)
    await _add_volunteer(session, "ada.b@example.dev")
    await _add_user(session, "dev@kvarteret.dev")  # the dev admin
    await _add_user(session, "demo.leder@example.dev")
    await ensure_only_fake_people(session)


@pytest.mark.asyncio
async def test_a_real_volunteer_stops_the_seed_even_on_localhost(session) -> None:
    """A tunnel on localhost that leads to a real database passes the URL check, and stops here."""
    assert check_database_target("postgresql+asyncpg://u:p@localhost:5432/db", None) == "localhost"
    await _add_volunteer(session, "someone@gmail.com")
    with pytest.raises(SeedRefused, match="1 volunteer"):
        await ensure_only_fake_people(session)


@pytest.mark.asyncio
async def test_a_volunteer_without_an_email_counts_as_real(session) -> None:
    await _add_volunteer(session, None)
    with pytest.raises(SeedRefused, match="no email"):
        await ensure_only_fake_people(session)


@pytest.mark.asyncio
async def test_a_real_user_account_stops_the_seed(session) -> None:
    await _add_user(session, "leader@samfunnet.example")
    with pytest.raises(SeedRefused, match="user account"):
        await ensure_only_fake_people(session)


@pytest.mark.asyncio
async def test_the_configured_dev_admin_is_allowed_whatever_the_address(session, monkeypatch) -> None:
    monkeypatch.setenv("DEV_ADMIN_EMAIL", "Morten.Dev@Example.Test")
    await _add_user(session, "morten.dev@example.test")
    await ensure_only_fake_people(session)
