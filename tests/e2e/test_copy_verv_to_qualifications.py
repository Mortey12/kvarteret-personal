"""scripts/shifts/copy_verv_to_qualifications.py on a real Postgres (made-up people).

Dry run writes nothing, --apply writes, a second --apply adds nothing, and
--apply is refused without COPY_CONFIRM. Needs E2E_DATABASE_URL, see conftest.py.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.shared.semester import get_current_semester_code
from scripts.shifts import copy_verv_to_qualifications as copy
from tests.e2e.conftest import E2E_DATABASE_URL, requires_e2e_database

pytestmark = requires_e2e_database

PARENT, STJERNE, KJOKKEN = 1, 2, 3
SIGRID, SANDER, TORA, KARI, PIA = 10, 11, 12, 13, 14


async def _seed(engine) -> None:
    current = get_current_semester_code()
    older = current - 10
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO public.groups (id, slug, name, is_active, active_through_semester, parent_group_id) VALUES "
            "(1, 'skjenke', 'Skjenke', true, 20992, NULL), "
            "(2, 'stjerne', 'Stjerne', true, 20992, 1), (3, 'kjokken', 'Kjøkken', true, 20992, 1)"
        ))
        await conn.execute(text(
            "INSERT INTO public.volunteer_records (id, last_name, gender) VALUES "
            "(10, 'A', 'A'), (11, 'B', 'A'), (12, 'C', 'A'), (13, 'D', 'A'), (14, 'E', 'A')"
        ))
        await conn.execute(text(
            "INSERT INTO public.assignment_roles (id, name, group_id, penguin_points) VALUES "
            "(1, 'Skiftleder', 2, 1), (2, 'Skjenker', 2, 1), (3, 'Opplæring', 2, 1), "
            "(4, 'Kokk', 3, 1), (5, 'Medlem', 1, 1)"
        ))
        await conn.execute(text(
            "INSERT INTO public.shift_templates "
            "(id, group_id, venue, title, weekday, start_time, end_time, training_replaces_slot, is_active) VALUES "
            "(1, 2, 'Stjernesalen', 'Skift', 1, '11:30', '17:00', true, true), "
            "(2, 3, 'Kjøkkenet', 'Skift', 1, '11:30', '17:00', true, true)"
        ))
        await conn.execute(text(
            "INSERT INTO public.shift_template_slots (template_id, kind, count, open_for_samf, sort_order) VALUES "
            "(1, 'skjenker', 1, false, 0), (2, 'kokk', 1, false, 0)"
        ))
        await conn.execute(text(
            "INSERT INTO public.role_assignments (volunteer_id, group_id, role_id, semester, contract_signed) VALUES "
            "(10, 2, 1, :cur, true), (11, 2, 2, :old, true), (12, 2, 3, :cur, true), "
            "(13, 3, 4, :cur, true), (14, 1, 5, :cur, true)"
        ), {"cur": current, "old": older})


async def _rows(engine) -> list[tuple]:
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT volunteer_id, qualification, status, group_id, training_target, takes_trainees, "
            "approved_by IS NULL, approved_at IS NOT NULL FROM public.volunteer_qualifications "
            "ORDER BY volunteer_id, qualification, group_id"
        ))
        return [tuple(r) for r in result.all()]


async def test_dry_run_apply_and_a_second_apply(clean_database, e2e_engine) -> None:
    await _seed(e2e_engine)
    host = make_url(E2E_DATABASE_URL).host or ""

    dry = await copy.copy_verv(E2E_DATABASE_URL, apply=False, env={})
    assert dry.new_rows == 5 and dry.applied is False
    assert await _rows(e2e_engine) == []

    first = await copy.copy_verv(E2E_DATABASE_URL, apply=True, env={"COPY_CONFIRM": host})
    assert first.new_rows == 5
    assert await _rows(e2e_engine) == [
        (SIGRID, "skiftleder", "approved", STJERNE, None, True, True, True),
        (SIGRID, "skjenker", "approved", STJERNE, None, False, True, True),
        (SANDER, "skjenker", "approved", STJERNE, None, False, True, True),
        (TORA, "skjenker", "training", STJERNE, 3, False, True, False),
        (KARI, "kokk", "approved", KJOKKEN, None, True, True, True),
    ]

    second = await copy.copy_verv(E2E_DATABASE_URL, apply=True, env={"COPY_CONFIRM": host})
    assert second.new_rows == 0 and second.existing_rows == 5
    assert len(await _rows(e2e_engine)) == 5
    assert second.result.ancestor_only_people == [PIA]


async def test_apply_is_refused_without_confirmation(clean_database, e2e_engine) -> None:
    await _seed(e2e_engine)

    assert copy.main(["--apply"], {"DATABASE_URL": E2E_DATABASE_URL}) == 2
    assert copy.main(["--apply"], {"DATABASE_URL": E2E_DATABASE_URL, "COPY_CONFIRM": "other.invalid"}) == 2
    assert await _rows(e2e_engine) == []
