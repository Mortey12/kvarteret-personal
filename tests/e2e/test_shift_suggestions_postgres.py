"""Pending suggestions on a real Postgres (seeded fake data): the table from migration
20261104_1200, the repository, accepting through the real rules, and the publishing refusal.

Needs ``E2E_DATABASE_URL`` (a migrated scratch Postgres); see ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.auth.roles import UserRole
from app.db.session import session_scope
from app.domain.shifts.errors import ShiftPlacementError
from app.domain.shifts.planning import monday_of, open_places
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.service import SUGGESTIONS_PENDING, ShiftsService
from app.domain.shifts.tables import shift_assignments, shift_suggestions
from app.domain.shifts.week_placing import accept_suggestions
from tests.e2e.conftest import requires_e2e_database
from tests.e2e.test_leader_write_race import runtime  # noqa: F401  (the seeded database fixture)
from tests.support.helpers import make_authenticated_user

pytestmark = requires_e2e_database


async def _scope(service):
    user = make_authenticated_user(UserRole.ADMIN)
    return await service.manageable_scope(user.role, user.auth_user_id)


async def _candidate(repository):
    """(group, monday, slot, volunteer): an empty skjenker place and a skjenker from another day."""
    today = datetime.now(UTC).date()
    for weeks_ahead in range(1, 6):
        monday = monday_of(today + timedelta(weeks=weeks_ahead))
        start = datetime.combine(monday, datetime.min.time(), tzinfo=UTC)
        for group in await repository.list_shift_groups():
            shifts = await repository.list_shifts_between(
                start, start + timedelta(days=7), group.group_id
            )
            for shift in shifts:
                for slot in shift.slots:
                    if (
                        slot.kind != "skjenker"
                        or open_places(shift, slot, count_absent=True) <= 0
                    ):
                        continue
                    for other in shifts:
                        if (
                            other.shift_id == shift.shift_id
                            or other.starts_at.date() == shift.starts_at.date()
                        ):
                            continue
                        for other_slot in other.slots:
                            if other_slot.kind == "skjenker" and other_slot.people:
                                person = other_slot.people[0]
                                on_here = {
                                    p.volunteer_id
                                    for s in shift.slots
                                    for p in s.people
                                }
                                if person.volunteer_id not in on_here:
                                    return (
                                        group.group_id,
                                        monday,
                                        slot.slot_id,
                                        person.volunteer_id,
                                    )
    pytest.fail("the seed has no empty skjenker place with a skjenker from another day")


async def test_save_list_accept_and_the_publish_refusal(runtime) -> None:  # noqa: F811
    repository = ShiftsRepository()
    service = ShiftsService(repository)
    async with session_scope(runtime):
        group_id, monday, slot_id, volunteer_id = await _candidate(repository)
        scope = await _scope(service)
        saved = await service.save_suggestions(
            group_id=group_id,
            week=monday,
            items=[(slot_id, volunteer_id, "Valgt fordi han vil")],
            scope=scope,
        )
        assert saved == 1

    async with session_scope(runtime):
        scope = await _scope(service)
        pending = await service.list_suggestions(group_id, monday)
        assert [(p.slot_id, p.volunteer_id, p.why) for p in pending] == [
            (slot_id, volunteer_id, "Valgt fordi han vil")
        ]
        # Not an assignment: the volunteer app reads assignments, so it never sees this.
        placed = await runtime_rows(
            shift_assignments.c.id,
            shift_assignments.c.slot_id == slot_id,
            shift_assignments.c.volunteer_id == volunteer_id,
        )
        assert placed == []
        with pytest.raises(ShiftPlacementError) as refused:
            await service.set_week_status(
                group_id=group_id,
                week=monday,
                status="published",
                whole_month=False,
                scope=scope,
            )
        assert str(refused.value) == SUGGESTIONS_PENDING

    async with session_scope(runtime):
        scope = await _scope(service)
        result = await accept_suggestions(
            service, group_id=group_id, week=monday, scope=scope
        )
        assert result.placed == 1, result
        assert await service.list_suggestions(group_id, monday) == []
        placed = await runtime_rows(
            shift_assignments.c.id,
            shift_assignments.c.slot_id == slot_id,
            shift_assignments.c.volunteer_id == volunteer_id,
        )
        assert len(placed) == 1


async def runtime_rows(column, *conditions):
    from app.db.session import current_session

    session = current_session()
    return list(
        (await session.execute(select(column).where(*conditions))).scalars().all()
    )


async def test_one_suggestion_per_person_and_place_and_the_cascade(runtime) -> None:  # noqa: F811
    repository = ShiftsRepository()
    service = ShiftsService(repository)
    async with session_scope(runtime) as session:
        group_id, monday, slot_id, volunteer_id = await _candidate(repository)
        await repository.replace_suggestions([], [(slot_id, volunteer_id, "a")])
        await session.commit()
    with pytest.raises(IntegrityError):
        async with session_scope(runtime) as session:
            await session.execute(
                shift_suggestions.insert().values(
                    slot_id=slot_id, volunteer_id=volunteer_id, why="dobbelt"
                )
            )
    async with session_scope(runtime) as session:
        assert len(await runtime_rows(shift_suggestions.c.id)) == 1
        # Deleting the place takes its suggestions with it (ON DELETE CASCADE).
        await session.execute(
            text("DELETE FROM public.shift_slots WHERE id = :id"), {"id": slot_id}
        )
        await session.flush()
        assert await runtime_rows(shift_suggestions.c.id) == []
    del service, group_id, monday


async def test_generate_then_accept_on_real_data(runtime) -> None:  # noqa: F811
    """Wishes for one bar's coming week, then "Lag forslag" and "Godta forslag" end to end."""
    from sqlalchemy import insert

    from app.db.session import current_session
    from app.domain.shifts.suggesting import generate_suggestions
    from app.domain.shifts.tables import shift_wishes
    from app.shared.semester import get_current_semester_code

    repository = ShiftsRepository()
    service = ShiftsService(repository)
    async with session_scope(runtime):
        group_id, monday, _, _ = await _candidate(repository)
        scope = await _scope(service)
        start = datetime.combine(monday, datetime.min.time(), tzinfo=UTC)
        shifts = await repository.list_shifts_between(
            start, start + timedelta(days=7), group_id
        )
        members = await repository.list_shift_members(
            get_current_semester_code(), await service._pool_group_ids(scope)
        )
        session = current_session()
        # Everyone in the pool "vil" or "kan" every shift of the bar's week.
        await session.execute(
            insert(shift_wishes),
            [
                {
                    "shift_id": s.shift_id,
                    "volunteer_id": m.volunteer_id,
                    "answer": "vil" if m.volunteer_id % 2 else "kan",
                }
                for s in shifts
                for m in members
            ],
        )

    async with session_scope(runtime):
        scope = await _scope(service)
        result = await generate_suggestions(
            service, group_id=group_id, week=monday, scope=scope
        )
        assert result.saved > 0, result
        pending = await service.list_suggestions(group_id, monday)
        assert len(pending) == result.saved
        assert all(item.why.startswith("Valgt fordi ") for item in pending)
        # Generating again gives exactly the same (deterministic), and replaces rather than adds.
        again = await generate_suggestions(
            service, group_id=group_id, week=monday, scope=scope
        )
        assert [
            (p.slot_id, p.volunteer_id)
            for p in await service.list_suggestions(group_id, monday)
        ] == [(p.slot_id, p.volunteer_id) for p in pending]
        assert again.saved == result.saved
        assigned_before = len(await runtime_rows(shift_assignments.c.id))

    async with session_scope(runtime):
        scope = await _scope(service)
        accepted = await accept_suggestions(
            service, group_id=group_id, week=monday, scope=scope
        )
        # The rules were asked when generating, so everything the engine proposed goes through.
        assert accepted.placed == result.saved and accepted.skipped == [], accepted
        assert (
            len(await runtime_rows(shift_assignments.c.id))
            == assigned_before + result.saved
        )
        assert await service.list_suggestions(group_id, monday) == []
