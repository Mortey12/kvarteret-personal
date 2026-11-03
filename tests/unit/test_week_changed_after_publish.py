"""The two new columns on shift_week_status: the migration, and the repository's SQL for them."""

from __future__ import annotations

from datetime import date
from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Integer, create_engine, insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import reset_request_session, set_request_session
from app.db.tables import public_metadata
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.tables import shift_week_status

MONDAY, NEXT = date(2026, 11, 2), date(2026, 11, 9)


def _run(connection, monkeypatch, direction: str) -> None:
    migration = import_module("migrations.versions.20261103_1200_week_changed_after_publish")
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
    getattr(migration, direction)()


def _old_table():
    connection = create_engine("sqlite://").connect()
    connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
    connection.execute(
        text(
            "CREATE TABLE public.shift_week_status (group_id INTEGER, week_start DATE, status TEXT, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (group_id, week_start))"
        )
    )
    connection.execute(text("INSERT INTO public.shift_week_status (group_id, week_start, status) VALUES (7, '2026-11-02', 'published')"))
    return connection


def test_upgrade_adds_two_empty_columns_and_leaves_rows_alone(monkeypatch) -> None:
    connection = _old_table()
    _run(connection, monkeypatch, "upgrade")
    row = connection.execute(
        text("SELECT status, changed_after_publish_at, changed_after_publish_by FROM public.shift_week_status")
    ).one()
    assert row == ("published", None, None)


def test_downgrade_removes_them_and_keeps_the_rows(monkeypatch) -> None:
    connection = _old_table()
    _run(connection, monkeypatch, "upgrade")
    _run(connection, monkeypatch, "downgrade")
    columns = [r[1] for r in connection.execute(text("PRAGMA public.table_info(shift_week_status)"))]
    assert "changed_after_publish_at" not in columns and "changed_after_publish_by" not in columns
    assert connection.execute(text("SELECT status FROM public.shift_week_status")).scalar_one() == "published"


@pytest.fixture
async def repo(monkeypatch):
    for column in shift_week_status.columns:
        if column.name == "group_id":
            monkeypatch.setattr(column, "type", column.type.with_variant(Integer, "sqlite"))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", execution_options={"schema_translate_map": {"public": None}})
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: public_metadata.create_all(conn, tables=[shift_week_status]))
    async with async_sessionmaker(engine)() as db:
        token = set_request_session(db)
        yield ShiftsRepository(), db
        reset_request_session(token)
    await engine.dispose()


async def _rows(db):
    return (await db.execute(text("SELECT group_id, week_start, status, changed_after_publish_by FROM shift_week_status ORDER BY 2"))).all()


async def test_only_a_published_row_is_flagged(repo) -> None:
    repository, db = repo
    await db.execute(insert(shift_week_status).values(group_id=7, week_start=MONDAY, status="published"))
    await db.execute(insert(shift_week_status).values(group_id=7, week_start=NEXT, status="open"))

    await repository.mark_week_changed(7, MONDAY, "Stjerne Leder")
    await repository.mark_week_changed(7, NEXT, "Stjerne Leder")
    await repository.mark_week_changed(7, date(2026, 11, 16), "Stjerne Leder")  # no row: nothing happens

    changes = await repository.get_week_changes(7, [MONDAY, NEXT, date(2026, 11, 16)])
    assert list(changes) == [MONDAY] and changes[MONDAY][1] == "Stjerne Leder"
    assert len(await _rows(db)) == 2


async def test_setting_a_status_clears_the_flag(repo) -> None:
    repository, db = repo
    await db.execute(insert(shift_week_status).values(group_id=7, week_start=MONDAY, status="published"))
    await repository.mark_week_changed(7, MONDAY, "Stjerne Leder")

    await repository.set_week_statuses(7, [MONDAY], "published")

    assert await repository.get_week_changes(7, [MONDAY]) == {}
    assert (await _rows(db))[0][3] is None
