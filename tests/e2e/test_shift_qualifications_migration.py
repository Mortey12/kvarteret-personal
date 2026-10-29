"""Migration 20261101_1200 on a real Postgres: schema only, no data is copied.

Replays the migration on a database that is already at head: downgrade drops
the new table and columns, upgrade creates them again. The verv copy is a
separate script, see test_copy_verv_to_qualifications.py.
Needs E2E_DATABASE_URL, see tests/e2e/conftest.py.
"""

from __future__ import annotations

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.e2e.conftest import requires_e2e_database

pytestmark = requires_e2e_database


async def test_upgrade_creates_the_schema_and_copies_nothing(clean_database, e2e_engine, monkeypatch):
    async with e2e_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO public.groups (id, slug, name, is_active, active_through_semester) VALUES "
            "(1, 'stjerne', 'Stjerne', true, 20992), (2, 'grondahls', 'Grøndahls', true, 20992)"
        ))
        await conn.execute(text(
            "INSERT INTO public.volunteer_records (id, last_name, gender) VALUES (10, 'Skiftleder', 'A')"
        ))
        await conn.execute(text(
            "INSERT INTO public.assignment_roles (id, name, group_id, penguin_points) "
            "VALUES (1, 'Skiftleder', 1, 1)"
        ))
        await conn.execute(text(
            "INSERT INTO public.shift_templates "
            "(id, group_id, venue, title, weekday, start_time, end_time, training_replaces_slot, is_active) "
            "VALUES (1, 1, 'Stjernesalen', 'Skift', 1, '11:30', '17:00', true, true)"
        ))
        await conn.execute(text(
            "INSERT INTO public.shift_template_slots (template_id, kind, count, open_for_samf, sort_order) "
            "VALUES (1, 'skjenker', 1, false, 0)"
        ))
        await conn.execute(text(
            "INSERT INTO public.shift_group_settings (group_id, app_enabled, samf_enabled) "
            "VALUES (2, true, false)"
        ))
        await conn.execute(text(
            "INSERT INTO public.role_assignments (volunteer_id, group_id, role_id, semester, contract_signed) "
            "VALUES (10, 1, 1, 20262, true)"
        ))
        migration = importlib.import_module("migrations.versions.20261101_1200_shift_qualifications")

        def replay(connection):
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            migration.downgrade()
            migration.upgrade()

        await conn.run_sync(replay)

    assert await _fetch_all(e2e_engine, "SELECT id FROM public.volunteer_qualifications") == []
    settings = await _fetch_all(e2e_engine,
        "SELECT group_id, training_shifts_needed, sibling_skiftleder_allowed "
        "FROM public.shift_group_settings"
    )
    assert settings == [{"group_id": 2, "training_shifts_needed": 3, "sibling_skiftleder_allowed": False}]
    columns = await _fetch_all(e2e_engine,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'shift_assignments' AND column_name = 'training_for'"
    )
    assert len(columns) == 1


async def _fetch_all(engine, sql: str) -> list[dict]:
    async with engine.connect() as conn:
        return [dict(row) for row in (await conn.execute(text(sql))).mappings().all()]
