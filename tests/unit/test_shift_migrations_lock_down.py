"""The tables the shift migrations create are closed to Supabase's public roles, like all others."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest

MIGRATIONS = [
    ("migrations.versions.20261029_1200_shift_week_status", ["shift_week_status", "shift_month_status"]),
    ("migrations.versions.20261030_1200_shift_closed_periods", ["shift_closed_periods"]),
    ("migrations.versions.20261104_1200_shift_suggestions", ["shift_suggestions"]),
]


class _RecordingOp:
    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        self.statements: list[str] = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


@pytest.mark.parametrize(("module", "tables"), MIGRATIONS)
def test_postgres_gets_row_level_security_and_a_revoke(module, tables, monkeypatch) -> None:
    migration = import_module(module)
    for table in tables:
        recorder = _RecordingOp("postgresql")
        monkeypatch.setattr(migration, "op", recorder)
        migration._lock_down(table)
        assert recorder.statements == [
            f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY",
            f"REVOKE ALL ON public.{table} FROM PUBLIC, anon, authenticated",
        ]


@pytest.mark.parametrize(("module", "tables"), MIGRATIONS)
def test_sqlite_gets_nothing(module, tables, monkeypatch) -> None:
    migration = import_module(module)
    recorder = _RecordingOp("sqlite")
    monkeypatch.setattr(migration, "op", recorder)
    migration._lock_down(tables[0])
    assert recorder.statements == []


def test_every_table_created_by_the_two_migrations_is_locked_down() -> None:
    """The upgrade and downgrade bodies call the helper for each table they create."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    week = (root / "20261029_1200_shift_week_status.py").read_text()
    periods = (root / "20261030_1200_shift_closed_periods.py").read_text()
    assert '_lock_down("shift_week_status")' in week
    assert '_lock_down("shift_month_status")' in week
    assert '_lock_down("shift_closed_periods")' in periods
