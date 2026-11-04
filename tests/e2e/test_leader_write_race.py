"""Two leaders writing to the same week at the same time, on a real Postgres (made-up data).

Both have the board as it was (the same ``If-Match``). The first write must win and the second must
get a 409, because the version check runs under a lock for the bar and week. Before that lock, both
passed the check and both wrote. Also: removing a person must not hit a person who was moved.

Needs ``E2E_DATABASE_URL`` (a migrated scratch Postgres); see ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.api.v1 import leader_shifts
from app.auth.roles import UserRole
from app.config import Settings
from app.db.session import build_database_runtime, session_scope
from app.domain.shifts.planning import monday_of
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.tables import shift_assignments, shift_slots, shifts
from tests.e2e.conftest import E2E_DATABASE_URL, requires_e2e_database
from tests.support.helpers import make_authenticated_user

pytestmark = requires_e2e_database

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "dev"))


@pytest.fixture
async def runtime(e2e_engine):
    from seed_shifts import seed_shifts  # noqa: PLC0415
    from seed_synthetic import seed  # noqa: PLC0415

    # The seed writes to many tables, so start from an empty database (the e2e database is
    # throwaway; only the migration version is kept).
    async with e2e_engine.begin() as conn:
        names = (
            await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'")
            )
        ).scalars().all()
        await conn.execute(
            text("TRUNCATE TABLE " + ", ".join(f"public.{n}" for n in names) + " RESTART IDENTITY CASCADE")
        )
    await seed(E2E_DATABASE_URL)
    await seed_shifts(E2E_DATABASE_URL)
    runtime = build_database_runtime(Settings(database_url=E2E_DATABASE_URL, app_env="development"))
    try:
        yield runtime
    finally:
        await runtime.aclose()


async def _two_placements_in_one_week(runtime):
    """(group_id, week_start, [(assignment_id, slot_id), (assignment_id, slot_id)]) for a coming week."""
    today = datetime.now(UTC).date()
    async with session_scope(runtime) as session:
        for weeks_ahead in range(1, 6):
            monday = monday_of(today + timedelta(weeks=weeks_ahead))
            start = datetime.combine(monday, datetime.min.time(), tzinfo=UTC)
            rows = (
                await session.execute(
                    select(shifts.c.group_id, shift_assignments.c.id, shift_assignments.c.slot_id)
                    .select_from(
                        shift_assignments.join(shift_slots, shift_slots.c.id == shift_assignments.c.slot_id).join(
                            shifts, shifts.c.id == shift_slots.c.shift_id
                        )
                    )
                    .where(shifts.c.starts_at >= start, shifts.c.starts_at < start + timedelta(days=7))
                    .order_by(shifts.c.group_id, shift_assignments.c.id)
                )
            ).all()
            by_group: dict[int, list] = {}
            for group_id, assignment_id, slot_id in rows:
                by_group.setdefault(group_id, []).append((assignment_id, slot_id))
            for group_id, placements in by_group.items():
                if len(placements) >= 2:
                    return group_id, monday, placements[:2]
    pytest.fail("the seed has no week with two placements in one bar")


class Leader:
    """One leader's request: its own session, as in a real request."""

    def __init__(self, runtime, service, group_id, week, if_match) -> None:
        self.runtime, self.service = runtime, service
        self.group_id, self.week, self.if_match = group_id, week, if_match

    async def remove(self, assignment_id: int, slot_id: int) -> int:
        user = make_authenticated_user(UserRole.ADMIN)
        scope = await self.service.manageable_scope(user.role, user.auth_user_id)
        try:
            async with session_scope(self.runtime):
                await leader_shifts._write(
                    None, self.service, scope, user,
                    group_id=self.group_id, week_start=self.week, if_match=self.if_match,
                    action="shift.remove", subject_type="shift_assignment", subject_id=assignment_id,
                    details={}, run=lambda: self.service.remove(assignment_id=assignment_id, scope=scope),
                )
        except HTTPException as refused:
            return refused.status_code
        return 200


async def _rows(runtime) -> list[int]:
    async with session_scope(runtime) as session:
        return sorted((await session.execute(select(shift_assignments.c.id))).scalars().all())


async def test_two_leaders_with_the_same_version_cannot_both_write(runtime, monkeypatch) -> None:
    monkeypatch.setattr(leader_shifts, "log_admin_activity", lambda **kwargs: None)
    service = ShiftsService(ShiftsRepository())
    group_id, week, placements = await _two_placements_in_one_week(runtime)
    user = make_authenticated_user(UserRole.ADMIN)
    scope = await service.manageable_scope(user.role, user.auth_user_id)
    async with session_scope(runtime):
        version = (await leader_shifts._board(service, scope, week=week, group_id=group_id)).version

    # Hold each request after it has read the board, so both have the same one when they compare.
    # (With the lock the second request cannot get here until the first is done: it gives up waiting.)
    arrived = 0
    both_here = asyncio.Event()
    real_board = leader_shifts._board

    async def board_then_wait(*args, **kwargs):
        nonlocal arrived
        board = await real_board(*args, **kwargs)
        arrived += 1
        if arrived == 2:
            both_here.set()
        try:
            await asyncio.wait_for(both_here.wait(), timeout=0.7)
        except TimeoutError:
            pass
        return board

    monkeypatch.setattr(leader_shifts, "_board", board_then_wait)

    one = Leader(runtime, service, group_id, week, version)
    two = Leader(runtime, service, group_id, week, version)
    first, second = await asyncio.gather(
        one.remove(*placements[0]), two.remove(*placements[1])
    )

    assert sorted([first, second]) == [200, 409], (first, second)
    remaining = await _rows(runtime)
    assert sum(1 for assignment_id, _ in placements if assignment_id in remaining) == 1


async def test_remove_does_not_hit_a_person_who_was_moved(runtime) -> None:
    repository = ShiftsRepository()
    group_id, week, placements = await _two_placements_in_one_week(runtime)
    (assignment_id, old_slot), (_, other_slot) = placements
    if old_slot == other_slot:
        pytest.skip("the seed placed both people in one place")

    async with session_scope(runtime):
        await repository.move_assignment(assignment_id, other_slot)  # another leader moved them
    async with session_scope(runtime):
        # The remove that was aimed at the old place must find nobody there.
        removed = await repository.remove_assignment_from_slot(assignment_id, old_slot)
    assert removed is False
    assert assignment_id in await _rows(runtime)

    async with session_scope(runtime):
        assert await repository.remove_assignment_from_slot(assignment_id, other_slot) is True
    assert assignment_id not in await _rows(runtime)
