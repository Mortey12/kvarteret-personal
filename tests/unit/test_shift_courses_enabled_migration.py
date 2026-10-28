from __future__ import annotations

from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def _run(connection, monkeypatch, direction: str) -> None:
    migration = import_module("migrations.versions.20261031_1200_shift_courses_enabled")
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
    getattr(migration, direction)()


def _connection():
    connection = create_engine("sqlite://").connect()
    # The migration works in the "public" schema; SQLite gets one by attaching.
    connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
    connection.execute(
        text(
            "CREATE TABLE public.shift_group_settings ("
            "group_id INTEGER PRIMARY KEY, app_enabled BOOLEAN NOT NULL, samf_enabled BOOLEAN NOT NULL, "
            "meeting_point TEXT)"
        )
    )
    connection.execute(text("CREATE TABLE public.group_course_requirements (course_id INTEGER, group_id INTEGER)"))
    connection.execute(
        text(
            "INSERT INTO public.shift_group_settings (group_id, app_enabled, samf_enabled) "
            "VALUES (1, 1, 1), (2, 1, 0), (3, 0, 0)"
        )
    )
    connection.execute(text("INSERT INTO public.group_course_requirements VALUES (10, 1), (11, 1)"))
    return connection


def test_every_existing_row_starts_with_the_switch_off(monkeypatch) -> None:
    connection = _connection()
    _run(connection, monkeypatch, "upgrade")

    rows = connection.execute(
        text("SELECT group_id, courses_enabled FROM public.shift_group_settings ORDER BY 1")
    ).all()
    assert [(group_id, bool(enabled)) for group_id, enabled in rows] == [(1, False), (2, False), (3, False)]  # group 1 has course requirements, still off


def test_new_rows_default_to_off_and_downgrade_drops_the_column(monkeypatch) -> None:
    connection = _connection()
    _run(connection, monkeypatch, "upgrade")
    connection.execute(
        text("INSERT INTO public.shift_group_settings (group_id, app_enabled, samf_enabled) VALUES (4, 1, 1)")
    )
    assert connection.execute(
        text("SELECT courses_enabled FROM public.shift_group_settings WHERE group_id = 4")
    ).scalar_one() in (0, False)

    _run(connection, monkeypatch, "downgrade")
    columns = [row[1] for row in connection.execute(text("PRAGMA public.table_info(shift_group_settings)"))]
    assert "courses_enabled" not in columns
