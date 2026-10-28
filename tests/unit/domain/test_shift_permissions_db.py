"""Who may plan which group, against real group rows in a (SQLite) database.

The group layout is in ``tests/support/shift_groups.py``; the ``session`` fixture is
in ``tests/unit/domain/conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update

from app.auth.models import AuthenticatedUser
from app.auth.roles import UserRole
from app.dependencies import get_shift_scope
from app.domain.admin_accounts.tables import group_admin_memberships
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import role_assignments
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.tables import shift_group_settings, shift_week_status
from app.domain.volunteers.tables import volunteer_records
from app.shared.semester import get_current_semester_code
from tests.support.shift_groups import (
    GRONDAHLS,
    HALVTIMEN,
    KULTUR,
    NAMES,
    PUBKJELLER,
    SKJENKE,
    SKJENKE_TREE,
    STJERNE,
    VAKT,
    VAKT_NORD,
)


def _user(role: UserRole) -> AuthenticatedUser:
    return AuthenticatedUser(
        auth_user_id=uuid4(),
        user_account_id=1,
        username="leder",
        email="leder@example.test",
        display_name="Leder",
        role=role,
    )


async def _scope(db, role: UserRole, *group_ids: int):
    user = _user(role)
    for group_id in group_ids:
        await db.execute(
            insert(group_admin_memberships).values(
                auth_user_id=user.auth_user_id, group_id=group_id, created_at=datetime.now(UTC)
            )
        )
    service = ShiftsService(ShiftsRepository())
    return await get_shift_scope(current_user=user, shifts_service=service)


@pytest.mark.asyncio
async def test_admin_gets_every_group(session) -> None:
    scope = await _scope(session, UserRole.ADMIN)
    assert scope.is_all
    assert all(scope.allows(group_id) for group_id in NAMES)


@pytest.mark.asyncio
async def test_gruppeadmin_of_skjenke_gets_the_participating_groups_below(session) -> None:
    scope = await _scope(session, UserRole.GROUP_ADMIN, SKJENKE)
    # Pubkjeller is below Grøndahls but does not take part.
    assert scope.group_ids == SKJENKE_TREE
    assert not scope.allows(KULTUR) and not scope.allows(PUBKJELLER)


@pytest.mark.asyncio
async def test_gruppeadmin_of_stjerne_gets_only_stjerne(session) -> None:
    assert (await _scope(session, UserRole.GROUP_ADMIN, STJERNE)).group_ids == {STJERNE}


@pytest.mark.asyncio
async def test_a_group_that_takes_part_through_a_descendant_counts(session) -> None:
    # Pubkjeller joins; Grøndahls takes part anyway, and its admin now covers both.
    await session.execute(
        insert(shift_group_settings).values(
            group_id=PUBKJELLER, app_enabled=False, samf_enabled=False
        )
    )
    scope = await _scope(session, UserRole.GROUP_ADMIN, GRONDAHLS)
    assert scope.group_ids == {GRONDAHLS, PUBKJELLER}


@pytest.mark.asyncio
async def test_non_skjenke_group_that_takes_part_gets_access(session) -> None:
    scope = await _scope(session, UserRole.GROUP_ADMIN, VAKT)
    assert scope.group_ids == {VAKT, VAKT_NORD}
    assert (await _scope(session, UserRole.GROUP_ADMIN, VAKT_NORD)).group_ids == {VAKT_NORD}


@pytest.mark.asyncio
async def test_several_assigned_groups_are_combined(session) -> None:
    scope = await _scope(session, UserRole.GROUP_ADMIN, STJERNE, HALVTIMEN)
    assert scope.group_ids == {STJERNE, HALVTIMEN}


@pytest.mark.asyncio
async def test_gruppeadmin_without_groups_gets_nothing(session) -> None:
    assert (await _scope(session, UserRole.GROUP_ADMIN)).is_empty


@pytest.mark.asyncio
async def test_gruppeadmin_of_a_non_participating_group_gets_nothing(session) -> None:
    assert (await _scope(session, UserRole.GROUP_ADMIN, KULTUR)).is_empty
    assert (await _scope(session, UserRole.GROUP_ADMIN, PUBKJELLER)).is_empty


@pytest.mark.asyncio
async def test_inactive_assigned_group_does_not_count(session) -> None:
    await session.execute(update(groups).where(groups.c.id == STJERNE).values(is_active=False))
    assert (await _scope(session, UserRole.GROUP_ADMIN, STJERNE)).is_empty


async def _member(db, volunteer_id: int, name: str, group_id: int) -> None:
    await db.execute(
        insert(volunteer_records).values(
            id=volunteer_id,
            first_name=name,
            last_name="Test",
            birth_date=date(2000, 1, 1),
            gender="A",
            created_at=datetime.now(UTC),
        )
    )
    await db.execute(
        insert(role_assignments).values(
            volunteer_id=volunteer_id,
            group_id=group_id,
            semester=get_current_semester_code(),
            contract_signed=True,
        )
    )


@pytest.mark.asyncio
async def test_pool_is_own_groups_plus_siblings_that_take_part(session) -> None:
    await _member(session, 1, "Stine", STJERNE)
    await _member(session, 2, "Gro", GRONDAHLS)
    await _member(session, 3, "Kari", KULTUR)  # does not take part
    await _member(session, 4, "Vera", VAKT)  # another tree
    await _member(session, 5, "Pia", PUBKJELLER)  # below Grøndahls, not participating
    service = ShiftsService(ShiftsRepository())

    stjerne = await _scope(session, UserRole.GROUP_ADMIN, STJERNE)
    plan = await service.get_week_plan(date(2026, 11, 2), scope=stjerne)
    assert {p.name for p in plan.pool} == {"Stine Test", "Gro Test"}
    assert [g.name for g in plan.groups] == []  # no templates in Stjerne, so no shifts to show

    vakt = await _scope(session, UserRole.GROUP_ADMIN, VAKT)
    assert {p.name for p in (await service.get_week_plan(date(2026, 11, 2), scope=vakt)).pool} == {
        "Vera Test"
    }

    admin = await _scope(session, UserRole.ADMIN)
    everyone = await service.get_week_plan(date(2026, 11, 2), scope=admin)
    assert {p.name for p in everyone.pool} == {"Stine Test", "Gro Test", "Vera Test"}

    unrelated = await _scope(session, UserRole.GROUP_ADMIN, KULTUR)
    assert (await service.get_week_plan(date(2026, 11, 2), scope=unrelated)).pool == []


@pytest.mark.asyncio
async def test_week_status_and_settings_are_upserted_in_the_database(session) -> None:
    service = ShiftsService(ShiftsRepository(), now=lambda: datetime(2026, 10, 28, 9, tzinfo=UTC))
    scope = await _scope(session, UserRole.GROUP_ADMIN, STJERNE)
    await service.set_week_status(
        group_id=STJERNE, week=date(2026, 11, 4), status="open", whole_month=False, scope=scope
    )
    await service.set_week_status(
        group_id=STJERNE, week=date(2026, 11, 2), status="published", whole_month=True, scope=scope
    )
    rows = (await session.execute(select(shift_week_status))).mappings().all()
    assert {(r["group_id"], r["week_start"], r["status"]) for r in rows} == {
        (STJERNE, date(2026, 11, day), "published") for day in (2, 9, 16, 23, 30)
    }
    for enabled in (True, False):
        await service.save_group_settings(
            STJERNE, app_enabled=enabled, samf_enabled=True, meeting_point=" Baren ", scope=scope
        )
    settings = (
        (await session.execute(select(shift_group_settings).where(shift_group_settings.c.group_id == STJERNE)))
        .mappings()
        .all()
    )
    assert len(settings) == 1
    assert settings[0]["app_enabled"] is False and settings[0]["meeting_point"] == "Baren"
    listed = await service.list_group_settings(scope)
    assert [(g.name, g.app_enabled, g.samf_enabled, g.meeting_point) for g in listed] == [
        ("Stjerne", False, True, "Baren")
    ]
