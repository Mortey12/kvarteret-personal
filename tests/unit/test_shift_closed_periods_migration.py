from __future__ import annotations

from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def _run(connection, monkeypatch, direction: str) -> None:
    migration = import_module("migrations.versions.20261030_1200_shift_closed_periods")
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
    getattr(migration, direction)()


def _connection():
    connection = create_engine("sqlite://").connect()
    # The migration works in the "public" schema; SQLite gets one by attaching.
    connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
    connection.execute(text("CREATE TABLE public.groups (id INTEGER PRIMARY KEY)"))
    connection.execute(text("INSERT INTO public.groups (id) VALUES (1)"))
    return connection


def _tables(connection) -> set[str]:
    return {r[0] for r in connection.execute(text("SELECT name FROM public.sqlite_master WHERE type='table'"))}


def test_revision_follows_the_week_status_migration() -> None:
    migration = import_module("migrations.versions.20261030_1200_shift_closed_periods")
    assert (migration.revision, migration.down_revision) == ("20261030_1200", "20261029_1200")


def test_upgrade_creates_the_table_with_its_date_check(monkeypatch) -> None:
    connection = _connection()
    _run(connection, monkeypatch, "upgrade")

    assert "shift_closed_periods" in _tables(connection)
    connection.execute(
        text(
            "INSERT INTO public.shift_closed_periods (id, group_id, starts_on, ends_on, label) "
            "VALUES (1, 1, '2026-12-19', '2027-01-05', 'Juleferie'), (2, 1, '2027-04-01', '2027-04-01', 'Fridag')"
        )
    )
    rows = connection.execute(text("SELECT label, created_at IS NOT NULL FROM public.shift_closed_periods ORDER BY id")).all()
    assert rows == [("Juleferie", 1), ("Fridag", 1)]
    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO public.shift_closed_periods (id, group_id, starts_on, ends_on, label) "
                "VALUES (3, 1, '2027-01-05', '2026-12-19', 'Baklengs')"
            )
        )


def test_downgrade_drops_the_table(monkeypatch) -> None:
    connection = _connection()
    _run(connection, monkeypatch, "upgrade")
    _run(connection, monkeypatch, "downgrade")
    assert "shift_closed_periods" not in _tables(connection)
