"""Group layout and an in-memory database for the Skiftlista permission tests.

A group takes part in Skiftlista when it has a shift_group_settings row or shift
templates, or has a descendant that does. Skjenkegruppen (no row of its own) takes
part through its subgroups; Pubkjeller (below Grøndahls) and Kultur do not. Vakt is
a second tree that has nothing to do with Skjenke.
"""

from __future__ import annotations

from datetime import time

from sqlalchemy import BigInteger, Integer, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.metadata import public_metadata
from app.db.session import reset_request_session, set_request_session
from app.domain.admin_accounts.tables import group_admin_memberships
from app.domain.courses.tables import courses
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.tables import (
    shift_assignments,
    shift_closed_periods,
    shift_group_settings,
    shift_slots,
    shift_template_slots,
    shift_templates,
    shift_week_status,
    shift_wishes,
    shifts,
)
from app.domain.volunteers.tables import volunteer_records

(SKJENKE, STJERNE, KJOKKEN, GRONDAHLS, HALVTIMEN, PUBKJELLER, KULTUR, VAKT, VAKT_NORD) = range(1, 10)
TABLES = [
    volunteer_records,
    groups,
    courses,
    assignment_roles,
    role_assignments,
    group_admin_memberships,
    shift_group_settings,
    shift_week_status,
    shift_closed_periods,
    shift_templates,
    shift_template_slots,
    shifts,
    shift_slots,
    shift_assignments,
    shift_wishes,
]
NAMES = {
    SKJENKE: ("Skjenkegruppen", "skjenke-gruppen", None),
    STJERNE: ("Stjerne", "stjerne", SKJENKE),
    KJOKKEN: ("Kjøkken", "kjokken", SKJENKE),
    GRONDAHLS: ("Grøndahls", "grondahls", SKJENKE),
    HALVTIMEN: ("Halvtimen", "halvtimen", SKJENKE),
    PUBKJELLER: ("Pubkjeller", "pubkjeller", GRONDAHLS),
    KULTUR: ("Kultur", "kultur", None),
    VAKT: ("Vakt", "vakt", None),
    VAKT_NORD: ("Vakt nord", "vakt-nord", VAKT),
}
SKJENKE_TREE = {SKJENKE, STJERNE, KJOKKEN, GRONDAHLS, HALVTIMEN}


async def shift_groups_session(monkeypatch):
    """Yield a session with the groups above; use from a pytest fixture."""
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
        # Takes part by a settings row: Stjerne, Vakt nord. By templates: the rest.
        for group_id in (STJERNE, VAKT_NORD):
            await db.execute(
                insert(shift_group_settings).values(
                    group_id=group_id, app_enabled=False, samf_enabled=False
                )
            )
        for group_id in (KJOKKEN, GRONDAHLS, HALVTIMEN, VAKT):
            await db.execute(
                insert(shift_templates).values(
                    group_id=group_id,
                    venue="Sted",
                    title="Skift",
                    weekday=0,
                    start_time=time(17),
                    end_time=time(23),
                    training_replaces_slot=True,
                    is_active=True,
                )
            )
        token = set_request_session(db)
        try:
            yield db
        finally:
            reset_request_session(token)
    await engine.dispose()
