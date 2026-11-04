"""scripts/shifts/pilot_readiness.py: the read-only readiness report, on SQLite with made-up data."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID

import pytest
from sqlalchemy import BigInteger, Integer, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.metadata import public_metadata
from app.domain.admin_accounts.tables import group_admin_memberships, user_accounts
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.tables import (
    shift_closed_periods,
    shift_group_settings,
    shift_template_slots,
    shift_templates,
    shifts,
    volunteer_qualifications,
)
from app.domain.volunteers.tables import volunteer_records
from app.shared.semester import get_current_semester_code
from scripts.shifts import pilot_readiness as readiness

RUN_AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
SEMESTER = get_current_semester_code(RUN_AT)
PARENT, STJERNE, GRONDAHLS = 1, 2, 3
LEADER_OK, LEADER_NO_LOGIN, LEADER_NO_ASSIGNMENT, TRAINEE, FAKE = 10, 11, 12, 13, 14
AUTH_OK = UUID("00000000-0000-0000-0000-00000000000a")
AUTH_NONE = UUID("00000000-0000-0000-0000-00000000000b")

TABLES = [
    volunteer_records,
    groups,
    assignment_roles,
    role_assignments,
    shift_templates,
    shift_template_slots,
    shifts,
    shift_group_settings,
    shift_closed_periods,
    volunteer_qualifications,
    user_accounts,
    group_admin_memberships,
]


@pytest.fixture
async def db(monkeypatch):
    for table in TABLES:
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                monkeypatch.setattr(
                    column, "type", column.type.with_variant(Integer, "sqlite")
                )
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"public": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: public_metadata.create_all(conn, tables=TABLES)
        )
    async with async_sessionmaker(engine)() as session:
        yield session
    await engine.dispose()


async def _world(db, *, with_fake_person: bool = False) -> None:
    """Skjenkegruppen with a working Stjerne and a Grøndahls that is not set up. No Halvtimen."""
    group = lambda i, slug, name, parent: {  # noqa: E731
        "id": i,
        "slug": slug,
        "name": name,
        "is_active": True,
        "active_through_semester": 20992,
        "parent_group_id": parent,
    }
    await db.execute(
        insert(groups),
        [
            group(PARENT, "skjenkegruppen", "Skjenkegruppen", None),
            group(STJERNE, "stjerne", "Stjerne", PARENT),
            group(GRONDAHLS, "grondahls", "Grøndahls", PARENT),
        ],
    )
    people = [
        (LEADER_OK, "ok@example.test"),
        (LEADER_NO_LOGIN, "nologin@example.test"),
        (LEADER_NO_ASSIGNMENT, "noassign@example.test"),
        (TRAINEE, None),
    ]
    if with_fake_person:
        people.append((FAKE, "someone@example.dev"))
    await db.execute(
        insert(volunteer_records),
        [
            {
                "id": v,
                "last_name": f"P{v}",
                "email": email,
                "gender": "A",
                "created_at": RUN_AT,
            }
            for v, email in people
        ],
    )
    await db.execute(
        insert(assignment_roles),
        [
            {"id": 1, "group_id": STJERNE, "name": "Skiftleder", "penguin_points": 1},
            {"id": 2, "group_id": STJERNE, "name": "Opplæring", "penguin_points": 1},
        ],
    )
    await db.execute(
        insert(role_assignments),
        [
            {
                "volunteer_id": v,
                "group_id": STJERNE,
                "role_id": 1,
                "semester": SEMESTER,
                "contract_signed": True,
            }
            for v in (LEADER_OK, LEADER_NO_LOGIN, LEADER_NO_ASSIGNMENT)
        ]
        + [
            {
                "volunteer_id": TRAINEE,
                "group_id": STJERNE,
                "role_id": 2,
                "semester": SEMESTER,
                "contract_signed": True,
            }
        ],
    )
    await db.execute(
        insert(shift_templates),
        {
            "id": 1,
            "group_id": STJERNE,
            "venue": "Stjerne",
            "title": "Dagskift",
            "weekday": 1,
            "start_time": time(18),
            "end_time": time(23),
            "training_replaces_slot": True,
            "is_active": True,
        },
    )
    await db.execute(
        insert(shift_template_slots),
        {
            "template_id": 1,
            "kind": "skjenker",
            "count": 1,
            "open_for_samf": False,
            "sort_order": 0,
        },
    )
    await db.execute(
        insert(shift_group_settings),
        {"group_id": STJERNE, "app_enabled": True, "samf_enabled": False},
    )
    await db.execute(
        insert(shifts),
        [
            {
                "group_id": STJERNE,
                "template_id": 1,
                "venue": "Stjerne",
                "title": "Dagskift",
                "starts_at": datetime(2026, 11, d, 17, tzinfo=UTC),
                "ends_at": datetime(2026, 11, d, 22, tzinfo=UTC),
                "is_closed": False,
            }
            for d in (3, 10)
        ],
    )
    await db.execute(
        insert(shift_closed_periods),
        {
            "group_id": STJERNE,
            "starts_on": date(2026, 12, 19),
            "ends_on": date(2027, 1, 10),
            "label": "Julestenging",
        },
    )
    await db.execute(
        insert(user_accounts),
        [
            {
                "volunteer_id": LEADER_OK,
                "auth_user_id": AUTH_OK,
                "username": "ok",
                "email": "ok@example.test",
                "role": "Gruppeadmin",
                "onboarding_status": "active",
                "created_at": RUN_AT,
                "updated_at": RUN_AT,
            },
            {
                "volunteer_id": LEADER_NO_ASSIGNMENT,
                "auth_user_id": AUTH_NONE,
                "username": "na",
                "email": "noassign@example.test",
                "role": "Gruppeadmin",
                "onboarding_status": "active",
                "created_at": RUN_AT,
                "updated_at": RUN_AT,
            },
        ],
    )
    # Assigned on the parent: covers every bar below it.
    await db.execute(
        insert(group_admin_memberships),
        {"auth_user_id": AUTH_OK, "group_id": PARENT, "created_at": RUN_AT},
    )
    await db.commit()


def _checks(report, name: str) -> dict[str, readiness.Check]:
    bar = next(b for b in report.bars if b.name == name)
    return {c.label: c for c in bar.checks}


async def _report(db, bars=("Stjerne", "Grøndahls", "Halvtimen")):
    return await readiness.build_report(db, bar_names=bars, now=RUN_AT)


async def test_a_prepared_bar_is_mostly_ok(db) -> None:
    await _world(db)

    checks = _checks(await _report(db), "Stjerne")

    assert checks["group exists and is active"].status == readiness.OK
    assert (
        checks["participating-group switch (app_enabled) is on"].status == readiness.OK
    )
    assert checks["active shift templates"].detail == "1"
    shifts_check = next(
        c for label, c in checks.items() if label.startswith("shifts from now")
    )
    assert shifts_check.status == readiness.OK and shifts_check.detail == "2"
    assert checks["closed periods (holidays) not yet ended"].detail == "1"
    assert checks["people with verv Skiftleder this semester"].detail == "3"
    assert checks["people with verv Opplæring this semester"].detail == "1"
    # 3 leaders get skjenker + skiftleder rows, the trainee one training row: 7.
    assert (
        checks["qualification rows the verv copy script would add"].detail
        == "7 (already present: 0)"
    )
    assert checks["people with a verv here and no email"].detail == "1"
    assert checks["people with a verv here and no email"].status == readiness.MISSING


async def test_leaders_without_login_or_assignment_are_counted(db) -> None:
    await _world(db)

    checks = _checks(await _report(db), "Stjerne")

    leaders = checks["skiftledere without a login / without Gruppeadmin for this bar"]
    assert leaders.status == readiness.MISSING
    assert leaders.detail.startswith(
        "1 without a login, 1 with a login but no Gruppeadmin"
    )
    # Only the account assigned on the parent covers the bar.
    assert checks["Gruppeadmin accounts covering this bar"].detail == "1"


async def test_a_bar_that_is_not_set_up_and_a_missing_bar_are_missing(db) -> None:
    await _world(db)
    report = await _report(db)

    grondahls = _checks(report, "Grøndahls")
    assert (
        grondahls["participating-group switch (app_enabled) is on"].status
        == readiness.MISSING
    )
    assert grondahls["active shift templates"].status == readiness.MISSING
    assert (
        grondahls[
            "the verv copy script treats this group as a bar or the kitchen"
        ].status
        == readiness.MISSING
    )

    halvtimen = _checks(report, "Halvtimen")
    assert list(halvtimen) == [f"group exists under {readiness.PARENT_GROUP_NAME}"]
    assert (
        halvtimen[f"group exists under {readiness.PARENT_GROUP_NAME}"].status
        == readiness.MISSING
    )
    assert report.missing_count() > 0


async def test_fake_people_are_reported_and_names_never_are(db) -> None:
    await _world(db, with_fake_person=True)

    report = await _report(db)
    text = readiness.format_report(report)

    fake = next(c for c in report.global_checks if "fake people" in c.label)
    assert fake.status == readiness.CHECK and fake.detail.startswith("1")
    assert "READ ONLY" in text
    for leaked in ("example.test", "someone@", "P10", "ok@"):
        assert leaked not in text


async def test_the_report_writes_nothing(db) -> None:
    await _world(db)
    tables = [t for t in TABLES]

    async def counts() -> list[int]:
        return [len((await db.execute(select(t))).all()) for t in tables]

    before = await counts()
    await _report(db)
    await db.rollback()
    assert await counts() == before


def test_a_missing_database_url_is_refused() -> None:
    assert readiness.main([], {}) == 2


def test_gruppeadmin_covers_the_group_and_its_subgroups() -> None:
    parent_of = {1: None, 2: 1, 3: 2}
    assert readiness.gruppeadmin_covers(3, {1}, parent_of)
    assert readiness.gruppeadmin_covers(2, {2}, parent_of)
    assert not readiness.gruppeadmin_covers(1, {2}, parent_of)
    assert not readiness.gruppeadmin_covers(2, set(), parent_of)


async def test_a_kitchen_needs_kokk_not_skiftleder(db) -> None:
    await _world(db)
    kitchen, cook = 4, 20
    await db.execute(
        insert(groups),
        {
            "id": kitchen,
            "slug": "kjokken",
            "name": "Kjøkken",
            "is_active": True,
            "active_through_semester": 20992,
            "parent_group_id": PARENT,
        },
    )
    await db.execute(
        insert(volunteer_records),
        {
            "id": cook,
            "last_name": "Cook",
            "email": "cook@example.test",
            "gender": "A",
            "created_at": RUN_AT,
        },
    )
    await db.execute(
        insert(assignment_roles),
        {"id": 3, "group_id": kitchen, "name": "Kokk", "penguin_points": 1},
    )
    await db.execute(
        insert(role_assignments),
        {
            "volunteer_id": cook,
            "group_id": kitchen,
            "role_id": 3,
            "semester": SEMESTER,
            "contract_signed": True,
        },
    )
    await db.execute(
        insert(shift_templates),
        {
            "id": 2,
            "group_id": kitchen,
            "venue": "Kjøkken",
            "title": "Kokk",
            "weekday": 2,
            "start_time": time(15),
            "end_time": time(20),
            "training_replaces_slot": True,
            "is_active": True,
        },
    )
    await db.execute(
        insert(shift_template_slots),
        {
            "template_id": 2,
            "kind": "kokk",
            "count": 1,
            "open_for_samf": False,
            "sort_order": 0,
        },
    )
    await db.commit()

    checks = _checks(await _report(db, bars=("Kjøkken",)), "Kjøkken")

    assert checks["people with verv Skiftleder this semester"].status == readiness.CHECK
    assert checks["people with verv Kokk this semester"].status == readiness.OK
