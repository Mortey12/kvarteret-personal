from __future__ import annotations

from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

MODULE = "migrations.versions.20261104_1200_shift_suggestions"


def _run(connection, monkeypatch, direction: str) -> None:
    migration = import_module(MODULE)
    monkeypatch.setattr(
        migration, "op", Operations(MigrationContext.configure(connection))
    )
    getattr(migration, direction)()


def _connection():
    connection = create_engine("sqlite://").connect()
    connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
    connection.execute(text("PRAGMA foreign_keys = ON"))
    connection.execute(text("CREATE TABLE public.shift_slots (id INTEGER PRIMARY KEY)"))
    connection.execute(
        text("CREATE TABLE public.volunteer_records (id INTEGER PRIMARY KEY)")
    )
    connection.execute(text("INSERT INTO public.shift_slots (id) VALUES (1), (2)"))
    connection.execute(
        text("INSERT INTO public.volunteer_records (id) VALUES (10), (11)")
    )
    return connection


def _tables(connection) -> set[str]:
    return {
        r[0]
        for r in connection.execute(
            text("SELECT name FROM public.sqlite_master WHERE type='table'")
        )
    }


def test_revision_follows_the_week_changed_migration() -> None:
    migration = import_module(MODULE)
    assert (migration.revision, migration.down_revision) == (
        "20261104_1200",
        "20261103_1200",
    )


def test_upgrade_creates_the_table_with_one_suggestion_per_person_and_place(
    monkeypatch,
) -> None:
    connection = _connection()
    _run(connection, monkeypatch, "upgrade")

    assert "shift_suggestions" in _tables(connection)
    connection.execute(
        text(
            "INSERT INTO public.shift_suggestions (id, slot_id, volunteer_id, why) "
            "VALUES (1, 1, 10, 'Valgt fordi han vil'), (2, 1, 11, 'b'), (3, 2, 10, 'c')"
        )
    )
    assert (
        connection.execute(
            text(
                "SELECT created_at IS NOT NULL FROM public.shift_suggestions WHERE id = 1"
            )
        ).scalar()
        == 1
    )
    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO public.shift_suggestions (id, slot_id, volunteer_id, why) VALUES (4, 1, 10, 'dobbelt')"
            )
        )
    with pytest.raises(IntegrityError):  # the reason is required
        connection.execute(
            text(
                "INSERT INTO public.shift_suggestions (id, slot_id, volunteer_id) VALUES (5, 2, 11)"
            )
        )


def test_a_suggestion_goes_when_its_place_goes(monkeypatch) -> None:
    connection = _connection()
    _run(connection, monkeypatch, "upgrade")
    connection.execute(
        text(
            "INSERT INTO public.shift_suggestions (id, slot_id, volunteer_id, why) VALUES (1, 1, 10, 'x'), (2, 2, 10, 'y')"
        )
    )

    connection.execute(text("DELETE FROM public.shift_slots WHERE id = 1"))

    assert connection.execute(
        text("SELECT id FROM public.shift_suggestions")
    ).scalars().all() == [2]


def test_downgrade_drops_the_table(monkeypatch) -> None:
    connection = _connection()
    _run(connection, monkeypatch, "upgrade")

    _run(connection, monkeypatch, "downgrade")

    assert "shift_suggestions" not in _tables(connection)
