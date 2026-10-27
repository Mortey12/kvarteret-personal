from __future__ import annotations

from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def _run(connection, monkeypatch, direction: str) -> None:
    migration = import_module("migrations.versions.20261029_1200_shift_week_status")
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
    getattr(migration, direction)()


def _engine_with_month_rows():
    engine = create_engine("sqlite://")
    connection = engine.connect()
    # The migration works in the "public" schema; SQLite gets one by attaching.
    connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
    connection.execute(text("CREATE TABLE public.groups (id INTEGER PRIMARY KEY)"))
    connection.execute(
        text(
            "CREATE TABLE public.shift_month_status ("
            "group_id INTEGER, month DATE, status TEXT, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (group_id, month))"
        )
    )
    connection.execute(
        text(
            "INSERT INTO public.shift_month_status (group_id, month, status) VALUES "
            "(1, '2026-11-01', 'published'), (1, '2026-12-01', 'open'), (2, '2026-11-01', 'allocating')"
        )
    )
    return connection


def test_month_rows_become_one_row_per_monday_in_the_month(monkeypatch) -> None:
    connection = _engine_with_month_rows()
    _run(connection, monkeypatch, "upgrade")

    rows = connection.execute(
        text("SELECT group_id, week_start, status FROM public.shift_week_status ORDER BY 1, 2")
    ).all()
    # Mondays: November 2, 9, 16, 23, 30 and December 7, 14, 21, 28 (2026).
    assert [(w, s) for g, w, s in rows if g == 1] == [
        ("2026-11-02", "published"),
        ("2026-11-09", "published"),
        ("2026-11-16", "published"),
        ("2026-11-23", "published"),
        ("2026-11-30", "published"),
        ("2026-12-07", "open"),
        ("2026-12-14", "open"),
        ("2026-12-21", "open"),
        ("2026-12-28", "open"),
    ]
    assert len([r for r in rows if r[0] == 2]) == 5
    tables = {r[0] for r in connection.execute(text("SELECT name FROM public.sqlite_master WHERE type='table'"))}
    assert "shift_month_status" not in tables


def test_downgrade_collapses_weeks_to_the_least_published_month(monkeypatch) -> None:
    connection = _engine_with_month_rows()
    _run(connection, monkeypatch, "upgrade")
    connection.execute(
        text("UPDATE public.shift_week_status SET status = 'allocating' WHERE week_start = '2026-11-16' AND group_id = 1")
    )
    _run(connection, monkeypatch, "downgrade")

    rows = connection.execute(
        text("SELECT group_id, month, status FROM public.shift_month_status ORDER BY 1, 2")
    ).all()
    assert rows == [(1, "2026-11-01", "allocating"), (1, "2026-12-01", "open"), (2, "2026-11-01", "allocating")]
