"""Who may plan which group, against real group rows in a (SQLite) database.

Skjenkegruppen has the subgroups Stjerne, Kjøkken, Grøndahls and Halvtimen;
Pubkjeller is a sub-subgroup of Grøndahls. Kultur is a separate group.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import BigInteger, Integer, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.models import AuthenticatedUser
from app.auth.roles import UserRole
from app.db.metadata import public_metadata
from app.db.session import reset_request_session, set_request_session
from app.dependencies import get_shift_scope
from app.domain.admin_accounts.tables import group_admin_memberships
from app.domain.groups.tables import groups
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.tables import shift_group_settings, shift_week_status

SKJENKE, STJERNE, KJOKKEN, GRONDAHLS, HALVTIMEN, PUBKJELLER, KULTUR = range(1, 8)
TABLES = [groups, group_admin_memberships, shift_group_settings, shift_week_status]
NAMES = {
    SKJENKE: ("Skjenkegruppen", "skjenke-gruppen", None),
    STJERNE: ("Stjerne", "stjerne", SKJENKE),
    KJOKKEN: ("Kjøkken", "kjokken", SKJENKE),
    GRONDAHLS: ("Grøndahls", "grondahls", SKJENKE),
    HALVTIMEN: ("Halvtimen", "halvtimen", SKJENKE),
    PUBKJELLER: ("Pubkjeller", "pubkjeller", GRONDAHLS),
    KULTUR: ("Kultur", "kultur", None),
}
SKJENKE_TREE = {SKJENKE, STJERNE, KJOKKEN, GRONDAHLS, HALVTIMEN, PUBKJELLER}


@pytest.fixture
async def session(monkeypatch):
    for table in TABLES:
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                monkeypatch.setattr(column, "type", column.type.with_variant(Integer, "sqlite"))
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"public": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: public_metadata.create_all(conn, tables=TABLES))
    async with async_sessionmaker(engine)() as db:
        for group_id, (name, slug, parent) in NAMES.items():
            await db.execute(
                insert(groups).values(
                    id=group_id,
                    slug=slug,
                    name=name,
                    is_active=True,
                    active_through_semester=20992,
                    parent_group_id=parent,
                )
            )
        token = set_request_session(db)
        try:
            yield db
        finally:
            reset_request_session(token)
    await engine.dispose()


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
    assert scope.group_ids is None
    assert all(scope.allows(group_id) for group_id in NAMES)


@pytest.mark.asyncio
async def test_gruppeadmin_of_skjenke_covers_all_its_subgroups(session) -> None:
    scope = await _scope(session, UserRole.GROUP_ADMIN, SKJENKE)
    assert scope.group_ids == SKJENKE_TREE
    assert not scope.allows(KULTUR)


@pytest.mark.asyncio
async def test_gruppeadmin_of_stjerne_gets_only_stjerne(session) -> None:
    scope = await _scope(session, UserRole.GROUP_ADMIN, STJERNE)
    assert scope.group_ids == {STJERNE}


@pytest.mark.asyncio
async def test_deeper_nesting_works(session) -> None:
    scope = await _scope(session, UserRole.GROUP_ADMIN, GRONDAHLS)
    assert scope.group_ids == {GRONDAHLS, PUBKJELLER}


@pytest.mark.asyncio
async def test_several_assigned_groups_are_combined(session) -> None:
    scope = await _scope(session, UserRole.GROUP_ADMIN, STJERNE, HALVTIMEN)
    assert scope.group_ids == {STJERNE, HALVTIMEN}


@pytest.mark.asyncio
async def test_gruppeadmin_without_groups_gets_nothing(session) -> None:
    scope = await _scope(session, UserRole.GROUP_ADMIN)
    assert scope.is_empty


@pytest.mark.asyncio
async def test_gruppeadmin_of_an_unrelated_group_gets_nothing(session) -> None:
    scope = await _scope(session, UserRole.GROUP_ADMIN, KULTUR)
    assert scope.is_empty


@pytest.mark.asyncio
async def test_inactive_assigned_group_does_not_count(session) -> None:
    await session.execute(update(groups).where(groups.c.id == STJERNE).values(is_active=False))
    assert (await _scope(session, UserRole.GROUP_ADMIN, STJERNE)).is_empty


@pytest.mark.asyncio
async def test_week_status_and_settings_are_upserted_in_the_database(session) -> None:
    service = ShiftsService(ShiftsRepository(), now=lambda: datetime(2026, 10, 28, 9, tzinfo=UTC))
    scope = await _scope(session, UserRole.GROUP_ADMIN, STJERNE)
    await service.set_week_status(
        group_id=STJERNE, week=date(2026, 11, 4), status="allocating", whole_month=False, scope=scope
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
    settings = (await session.execute(select(shift_group_settings))).mappings().all()
    assert len(settings) == 1
    assert settings[0]["app_enabled"] is False and settings[0]["meeting_point"] == "Baren"
    listed = await service.list_group_settings(scope)
    assert [(g.name, g.app_enabled, g.samf_enabled, g.meeting_point) for g in listed] == [
        ("Stjerne", False, True, "Baren")
    ]
