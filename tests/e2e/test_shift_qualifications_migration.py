"""Migration 20261101_1200 on a real Postgres seeded with verv (made-up people).

Replays the migration on a database that is already at head: downgrade drops
the new table and columns, upgrade creates them again and copies the verv.
Needs E2E_DATABASE_URL, see tests/e2e/conftest.py.
"""

from __future__ import annotations

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.shared.semester import get_current_semester_code
from tests.e2e.conftest import requires_e2e_database

pytestmark = requires_e2e_database

STJERNE, GRONDAHLS, KJOKKEN, KULTUR = 1, 2, 3, 4
SIGRID, SANDER, TORA, KARI, OLE, UNN = 10, 11, 12, 13, 14, 15


async def test_upgrade_copies_verv_into_qualifications(clean_database, e2e_engine, monkeypatch, capsys):
    current = get_current_semester_code()
    previous = current - 1 if current % 10 == 2 else current - 10 + 1
    older = previous - 1 if previous % 10 == 2 else previous - 10 + 1
    async with e2e_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO public.groups (id, slug, name, is_active, active_through_semester) VALUES "
            "(1, 'stjerne', 'Stjerne', true, 20992), (2, 'grondahls', 'Grøndahls', true, 20992), "
            "(3, 'kjokken', 'Kjøkken', true, 20992), (4, 'kultur', 'Kultur', true, 20992)"
        ))
        await conn.execute(text(
            "INSERT INTO public.volunteer_records (id, last_name, gender) VALUES "
            "(10, 'Skiftleder', 'A'), (11, 'Skjenker', 'A'), (12, 'Trainee', 'A'), "
            "(13, 'Kokk', 'A'), (14, 'Annen', 'A'), (15, 'Utenfor', 'A')"
        ))
        await conn.execute(text(
            "INSERT INTO public.assignment_roles (id, name, group_id, penguin_points) VALUES "
            "(1, 'Skiftleder', 1, 1), (2, 'Skjenker', 1, 1), (3, 'Opplæring', 1, 1), "
            "(4, 'Kokk', 3, 1), (5, 'Skiftleder (vikar)', 2, 1), (6, 'Medlem', 4, 1)"
        ))
        # Stjerne and Grøndahls have bar places, Kjøkken has kokk places only,
        # Kultur has no shifts at all.
        await conn.execute(text(
            "INSERT INTO public.shift_templates "
            "(id, group_id, venue, title, weekday, start_time, end_time, training_replaces_slot, is_active) VALUES "
            "(1, 1, 'Stjernesalen', 'Skift', 1, '11:30', '17:00', true, true), "
            "(2, 3, 'Kjøkkenet', 'Skift', 1, '11:30', '17:00', true, true)"
        ))
        await conn.execute(text(
            "INSERT INTO public.shift_template_slots (template_id, kind, count, open_for_samf, sort_order) VALUES "
            "(1, 'skjenker', 1, false, 0), (2, 'kokk', 1, false, 0)"
        ))
        await conn.execute(text(
            "INSERT INTO public.shift_group_settings (group_id, app_enabled, samf_enabled) "
            "VALUES (2, true, false)"
        ))
        await conn.execute(text(
            "INSERT INTO public.role_assignments (volunteer_id, group_id, role_id, semester, contract_signed) VALUES "
            "(10, 1, 1, :cur, true), (11, 1, 2, :prev, true), (12, 1, 3, :cur, true), "
            "(13, 3, 4, :cur, true), (14, 2, 5, :cur, true), (14, 2, NULL, :prev, true), "
            "(15, 4, 6, :cur, true), (10, 1, 1, :old, true), (11, 2, 1, :old, true)"
        ), {"cur": current, "prev": previous, "old": older})
        migration = importlib.import_module("migrations.versions.20261101_1200_shift_qualifications")

        def replay(connection):
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            migration.downgrade()
            migration.upgrade()

        await conn.run_sync(replay)

    rows = await _fetch_all(e2e_engine,
        "SELECT volunteer_id, qualification, status, group_id, training_target, takes_trainees, "
        "approved_by IS NULL AS no_approver, approved_at IS NOT NULL AS has_time "
        "FROM public.volunteer_qualifications ORDER BY volunteer_id, qualification, group_id"
    )
    assert [(r["volunteer_id"], r["qualification"], r["status"], r["group_id"]) for r in rows] == [
        (10, "skiftleder", "approved", 1),
        (10, "skjenker", "approved", 1),
        (11, "skjenker", "approved", 1),
        (12, "skjenker", "training", 1),
        (13, "kokk", "approved", 3),
        (14, "skjenker", "approved", 2),
    ]
    by_key = {(r["volunteer_id"], r["qualification"]): r for r in rows}
    assert by_key[(10, "skiftleder")]["takes_trainees"] is True
    assert by_key[(13, "kokk")]["takes_trainees"] is True
    assert by_key[(10, "skjenker")]["takes_trainees"] is False
    assert by_key[(12, "skjenker")]["training_target"] == 3
    assert all(r["no_approver"] for r in rows)
    assert {r["has_time"] for r in rows if r["status"] == "approved"} == {True}
    # Volunteer 15 is only in Kultur (no shifts) and verv older than the
    # previous semester was ignored, so no extra rows.
    printed = capsys.readouterr().out
    assert "'Skiftleder (vikar)': 1" in printed
    assert "'Skjenker': 1" in printed

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
