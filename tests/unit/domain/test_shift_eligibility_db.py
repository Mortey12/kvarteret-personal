"""Eligibility from qualifications on the leader board, against a (SQLite) database.

The group layout is in ``tests/support/shift_groups.py``: Stjerne, Kjøkken, Grøndahls
and Halvtimen are siblings below Skjenkegruppen. Made-up people only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import insert, update

from app.auth.models import AuthenticatedUser
from app.auth.roles import UserRole
from app.dependencies import get_shift_scope
from app.domain.admin_accounts.tables import group_admin_memberships
from app.domain.role_assignments.tables import role_assignments
from app.domain.shifts.errors import ShiftPlacementError
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.tables import (
    shift_group_settings,
    shift_slots,
    shifts,
    volunteer_qualifications,
)
from app.domain.volunteers.tables import volunteer_records
from app.shared.semester import get_current_semester_code
from tests.support.shift_groups import GRONDAHLS, KJOKKEN, SKJENKE, STJERNE

STINE, GRO, SKJENKELEDER, KAI = 1, 2, 3, 4


class CountingRepository(ShiftsRepository):
    def __init__(self) -> None:
        super().__init__()
        self.qualification_reads = 0

    async def list_qualifications(self, volunteer_ids):
        self.qualification_reads += 1
        return await super().list_qualifications(volunteer_ids)


async def _person(db, volunteer_id: int, name: str, group_id: int | None) -> None:
    await db.execute(
        insert(volunteer_records).values(
            id=volunteer_id,
            first_name=name,
            last_name="Test",
            birth_date=date(1999, 1, 1),
            gender="A",
            created_at=datetime.now(UTC),
        )
    )
    if group_id is not None:
        await db.execute(
            insert(role_assignments).values(
                volunteer_id=volunteer_id,
                group_id=group_id,
                semester=get_current_semester_code(),
                contract_signed=True,
            )
        )


async def _qualify(db, volunteer_id: int, qualification: str, status: str, group_id: int) -> None:
    await db.execute(
        insert(volunteer_qualifications).values(
            volunteer_id=volunteer_id,
            qualification=qualification,
            status=status,
            group_id=group_id,
            takes_trainees=False,
        )
    )


async def _scope(db, role: UserRole, *group_ids: int):
    user = AuthenticatedUser(
        auth_user_id=uuid4(),
        user_account_id=1,
        username="leder",
        email="leder@example.test",
        display_name="Leder",
        role=role,
    )
    for group_id in group_ids:
        await db.execute(
            insert(group_admin_memberships).values(
                auth_user_id=user.auth_user_id, group_id=group_id, created_at=datetime.now(UTC)
            )
        )
    return await get_shift_scope(current_user=user, shifts_service=ShiftsService(ShiftsRepository()))


async def _shift(db, group_id: int, kinds: tuple[str, ...]) -> dict[str, int]:
    shift_id = (
        await db.execute(
            insert(shifts)
            .values(
                group_id=group_id,
                venue="Sted",
                title="Skift",
                starts_at=datetime(2026, 11, 3, 17, tzinfo=UTC),
                ends_at=datetime(2026, 11, 3, 23, tzinfo=UTC),
                is_closed=False,
                training_replaces_slot=True,
            )
            .returning(shifts.c.id)
        )
    ).scalar_one()
    slot_ids = {}
    for order, kind in enumerate(kinds):
        slot_ids[kind] = (
            await db.execute(
                insert(shift_slots)
                .values(shift_id=shift_id, kind=kind, count=1, open_for_samf=False, sort_order=order)
                .returning(shift_slots.c.id)
            )
        ).scalar_one()
    return slot_ids


@pytest.mark.asyncio
async def test_a_sibling_bars_leader_places_by_qualification_not_verv(session) -> None:
    await _person(session, STINE, "Stine", STJERNE)
    await _person(session, GRO, "Gro", STJERNE)
    await _qualify(session, STINE, "skjenker", "approved", STJERNE)
    await _qualify(session, STINE, "skiftleder", "approved", STJERNE)
    await _qualify(session, GRO, "skiftleder", "approved", GRONDAHLS)  # "Godkjenn direkte"
    slots = await _shift(session, GRONDAHLS, ("skiftleder", "skjenker"))
    service = ShiftsService(ShiftsRepository())
    leader = await _scope(session, UserRole.GROUP_ADMIN, GRONDAHLS)

    # A skjenker row from a sibling bar works here.
    await service.assign(slot_id=slots["skjenker"], volunteer_id=STINE, scope=leader)
    # A skiftleder row for the shift's own group works for a member of a sibling only.
    await service.assign(slot_id=slots["skiftleder"], volunteer_id=GRO, scope=leader)


@pytest.mark.asyncio
async def test_sibling_skiftleder_needs_the_receiving_bars_switch(session) -> None:
    await _person(session, STINE, "Stine", STJERNE)
    await _qualify(session, STINE, "skiftleder", "approved", STJERNE)
    slots = await _shift(session, GRONDAHLS, ("skiftleder",))
    service = ShiftsService(ShiftsRepository())
    leader = await _scope(session, UserRole.GROUP_ADMIN, GRONDAHLS)

    with pytest.raises(ShiftPlacementError, match="Kun for godkjente skiftleder"):
        await service.assign(slot_id=slots["skiftleder"], volunteer_id=STINE, scope=leader)

    await session.execute(
        insert(shift_group_settings).values(
            group_id=GRONDAHLS, app_enabled=False, samf_enabled=False, sibling_skiftleder_allowed=True
        )
    )
    await service.assign(slot_id=slots["skiftleder"], volunteer_id=STINE, scope=leader)


@pytest.mark.asyncio
async def test_kokk_row_in_a_bar_does_not_work_in_the_kitchen_and_verv_is_ignored(session) -> None:
    await _person(session, STINE, "Stine", STJERNE)
    await _qualify(session, STINE, "kokk", "approved", STJERNE)
    slots = await _shift(session, KJOKKEN, ("kokk",))
    service = ShiftsService(ShiftsRepository())
    admin = await _scope(session, UserRole.ADMIN)
    with pytest.raises(ShiftPlacementError, match="Kun for godkjente kokk"):
        await service.assign(slot_id=slots["kokk"], volunteer_id=STINE, scope=admin)


@pytest.mark.asyncio
async def test_pool_tokens_follow_the_qualification_rows(session) -> None:
    await _person(session, STINE, "Stine", STJERNE)
    await _person(session, SKJENKELEDER, "Skjenkeleder", SKJENKE)  # registered in the parent only
    await _person(session, KAI, "Kai", STJERNE)  # member without any row
    await _qualify(session, STINE, "skjenker", "approved", STJERNE)
    await _qualify(session, SKJENKELEDER, "skjenker", "approved", GRONDAHLS)
    await _shift(session, GRONDAHLS, ("skjenker",))
    service = ShiftsService(ShiftsRepository())
    admin = await _scope(session, UserRole.ADMIN)

    plan = await service.get_week_plan(date(2026, 11, 2), scope=admin)
    tokens = {person.name: person.allowed_tokens for person in plan.pool}
    assert f"{GRONDAHLS}:skjenker:0" in tokens["Stine Test"]
    assert f"{GRONDAHLS}:skjenker:0" in tokens["Skjenkeleder Test"]
    assert f"{GRONDAHLS}:skjenker:0" not in tokens["Kai Test"]
    assert f"{GRONDAHLS}:skjenker:1" in tokens["Kai Test"]
    assert f"{GRONDAHLS}:skiftleder:0" not in tokens["Stine Test"]


@pytest.mark.asyncio
async def test_the_board_reads_qualifications_once_per_page(session) -> None:
    for volunteer_id, name in ((STINE, "Stine"), (GRO, "Gro"), (KAI, "Kai")):
        await _person(session, volunteer_id, name, STJERNE)
    await _shift(session, GRONDAHLS, ("skiftleder", "skjenker"))
    await _shift(session, KJOKKEN, ("kokk",))
    repository = CountingRepository()
    service = ShiftsService(repository)
    admin = await _scope(session, UserRole.ADMIN)

    plan = await service.get_week_plan(date(2026, 11, 2), scope=admin)
    assert len(plan.pool) == 3
    assert repository.qualification_reads == 1


@pytest.mark.asyncio
async def test_switch_default_is_off_without_a_settings_row(session) -> None:
    await session.execute(update(shift_group_settings).values(sibling_skiftleder_allowed=False))
    settings = await ShiftsRepository().get_qualification_settings([GRONDAHLS, STJERNE])
    assert not settings[GRONDAHLS].sibling_skiftleder_allowed
    assert not settings[STJERNE].sibling_skiftleder_allowed
