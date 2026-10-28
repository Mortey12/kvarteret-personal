"""scripts/dev/seed_demo.py: its safety checks, and what it adds, on a throwaway SQLite database."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import BigInteger, Integer, func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.metadata import public_metadata
from app.domain.courses.tables import course_completions, courses
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.tables import (
    shift_assignments,
    shift_availability_rules,
    shift_group_settings,
    shift_slots,
    shift_templates,
    shift_week_status,
    shift_wishes,
    shifts,
)
from app.domain.volunteers.tables import volunteer_records
from app.shared.semester import get_current_semester_code
from scripts.dev import seed_demo
from scripts.dev.seed_demo import (
    SWAP_MESSAGE,
    DemoSeedRefused,
    check_database_target,
    ensure_only_fake_people,
    seed_into,
)

TABLES = [
    volunteer_records,
    groups,
    assignment_roles,
    role_assignments,
    courses,
    course_completions,
    shift_templates,
    shifts,
    shift_slots,
    shift_assignments,
    shift_group_settings,
    shift_week_status,
    shift_availability_rules,
    shift_wishes,
]
CHECKS = pytest.mark.asyncio


# ------------------------------------------------------------------ safety


def test_local_hosts_need_no_confirmation() -> None:
    for url in (
        "postgresql+asyncpg://postgres:postgres@localhost:55432/dev",
        "postgresql+asyncpg://postgres:postgres@127.0.0.1/dev",
    ):
        assert check_database_target(url, None) in {"localhost", "127.0.0.1"}


def test_a_hosted_database_needs_the_exact_host_as_confirmation() -> None:
    url = "postgresql+asyncpg://demo:s3cret-pw@ep-demo-123.neon.example/demo?ssl=require"
    with pytest.raises(DemoSeedRefused, match="DEMO_SEED_CONFIRM=ep-demo-123.neon.example") as refused:
        check_database_target(url, None)
    assert "s3cret-pw" not in str(refused.value)
    for wrong in ("", "yes", "true", "ep-demo-123", "EP-DEMO-123.neon.example", "localhost"):
        with pytest.raises(DemoSeedRefused):
            check_database_target(url, wrong)
    assert check_database_target(url, "ep-demo-123.neon.example") == "ep-demo-123.neon.example"


def test_a_url_without_a_host_is_refused() -> None:
    for url in ("sqlite+aiosqlite:///demo.db", "sqlite+aiosqlite://"):
        with pytest.raises(DemoSeedRefused, match="no host"):
            check_database_target(url, "anything")


@CHECKS
async def test_seed_demo_refuses_a_non_local_url_before_it_connects() -> None:
    # No database exists at this host: the refusal must come first, not a connection error.
    with pytest.raises(DemoSeedRefused, match="DEMO_SEED_CONFIRM"):
        await seed_demo.seed_demo("postgresql+asyncpg://u:p@db.invalid/demo", env={})
    with pytest.raises(DemoSeedRefused):
        await seed_demo.seed_demo(
            "postgresql+asyncpg://u:p@db.invalid/demo", env={"DEMO_SEED_CONFIRM": "other.invalid"}
        )


@pytest.fixture
async def session(monkeypatch):
    for table in TABLES:
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                monkeypatch.setattr(column, "type", column.type.with_variant(Integer, "sqlite"))
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", execution_options={"schema_translate_map": {"public": None}}
    )
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: public_metadata.create_all(conn, tables=TABLES))
    async with async_sessionmaker(engine)() as db:
        yield db
    await engine.dispose()


async def _volunteer(db, email: str | None, first: str = "Test") -> int:
    return (
        await db.execute(
            insert(volunteer_records)
            .values(
                first_name=first, last_name="Person", email=email, gender="A", birth_date=date(1999, 1, 1),
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            .returning(volunteer_records.c.id)
        )
    ).scalar_one()


@CHECKS
async def test_a_real_looking_email_stops_the_seed_and_nothing_is_written(session) -> None:
    await _volunteer(session, "kari@example.dev")
    await _volunteer(session, "someone@gmail.test")
    with pytest.raises(DemoSeedRefused, match="1 volunteer") as refused:
        await seed_into(session, now=datetime.now(UTC))
    assert "gmail" not in str(refused.value)  # never print the address
    assert await session.scalar(select(func.count()).select_from(volunteer_records)) == 2


@CHECKS
async def test_a_volunteer_without_an_email_also_stops_the_seed(session) -> None:
    await _volunteer(session, None)
    with pytest.raises(DemoSeedRefused):
        await ensure_only_fake_people(session)


@CHECKS
async def test_fake_emails_pass_whatever_the_case(session) -> None:
    await _volunteer(session, "A.B@EXAMPLE.DEV")
    await ensure_only_fake_people(session)
    # Only the exact domain: a look-alike is not fake.
    await _volunteer(session, "x@notexample.dev.com")
    with pytest.raises(DemoSeedRefused):
        await ensure_only_fake_people(session)


# ------------------------------------------------------------- what it adds


async def _base(db, now: datetime) -> None:
    """A small stand-in for seed_synthetic + seed_shifts: groups, verv, members and shifts."""
    semester = get_current_semester_code()
    names = ["Skjenkegruppen", "Stjerne", "Kjøkken", "Grøndahls", "Halvtimen", "Lydgruppen", "Eksponeringsetaten"]
    group_ids = {}
    for name in names:
        group_ids[name] = (
            await db.execute(
                insert(groups)
                .values(
                    slug=name.lower(), name=name, is_active=True, active_through_semester=20991,
                    parent_group_id=group_ids.get("Skjenkegruppen") if name in names[1:5] else None,
                )
                .returning(groups.c.id)
            )
        ).scalar_one()
    roles = {
        "Stjerne": ("Skiftleder", "Skjenker", "Opplæring"),
        "Kjøkken": ("Kokk", "Opplæring"),
        "Grøndahls": ("Skiftleder", "Skjenker", "Opplæring"),
        "Halvtimen": ("Skiftleder", "Skjenker", "Opplæring"),
        "Lydgruppen": ("Frivillig",),
        "Eksponeringsetaten": ("Frivillig",),
    }
    role_ids = {}
    for group, role_names in roles.items():
        for role in role_names:
            role_ids[(group, role)] = (
                await db.execute(
                    insert(assignment_roles)
                    .values(group_id=group_ids[group], name=role, penguin_points=1)
                    .returning(assignment_roles.c.id)
                )
            ).scalar_one()
    member_of = [("Stjerne", "Skjenker")] * 6 + [("Grøndahls", "Skjenker")] * 5 + [("Kjøkken", "Kokk")] * 3
    member_of += [("Halvtimen", "Skjenker")] * 3
    for index, (group, role) in enumerate(member_of):
        vid = await _volunteer(db, f"base{index}@example.dev", first=f"Base{index}")
        await db.execute(
            insert(role_assignments).values(
                volunteer_id=vid, group_id=group_ids[group], role_id=role_ids[(group, role)],
                semester=semester, contract_signed=True,
            )
        )
    await db.execute(insert(courses).values(name="Brannkurs"))
    await db.execute(
        insert(shift_group_settings).values(
            group_id=group_ids["Stjerne"], app_enabled=True, samf_enabled=True, courses_enabled=False
        )
    )
    # Mondays, Wednesdays and Fridays for the next 110 days in the three groups with shifts.
    day = now.date()
    for offset in range(110):
        date_ = day + timedelta(days=offset)
        if date_.weekday() not in (0, 2, 4):
            continue
        for group in ("Stjerne", "Grøndahls", "Halvtimen"):
            starts_at = datetime.combine(date_, time(18, 0), tzinfo=UTC)
            shift_id = (
                await db.execute(
                    insert(shifts)
                    .values(
                        group_id=group_ids[group], venue=group, title="Skift", starts_at=starts_at,
                        ends_at=starts_at + timedelta(hours=5), is_closed=False, training_replaces_slot=True,
                    )
                    .returning(shifts.c.id)
                )
            ).scalar_one()
            for order, (kind, count) in enumerate((("skiftleder", 1), ("skjenker", 2))):
                await db.execute(
                    insert(shift_slots).values(
                        shift_id=shift_id, kind=kind, count=count, open_for_samf=False, sort_order=order
                    )
                )


async def _place_stjerne_people(db) -> None:
    """Existing placements, like seed_shifts makes: the first few Stjerne shifts have a skjenker."""
    stjerne = await db.scalar(select(groups.c.id).where(groups.c.name == "Stjerne"))
    slot_ids = (
        await db.execute(
            select(shift_slots.c.id)
            .select_from(shift_slots.join(shifts, shifts.c.id == shift_slots.c.shift_id))
            .where(shifts.c.group_id == stjerne, shift_slots.c.kind == "skjenker")
            .order_by(shifts.c.starts_at)
            .limit(4)
        )
    ).scalars().all()
    person = await db.scalar(select(func.min(volunteer_records.c.id)))
    for slot_id in slot_ids:
        await db.execute(
            insert(shift_assignments).values(slot_id=slot_id, volunteer_id=person, swap_requested=False)
        )


async def _counts(db) -> dict[str, int]:
    tables = {
        "volunteers": volunteer_records,
        "completions": course_completions,
        "rules": shift_availability_rules,
        "wishes": shift_wishes,
        "assignments": shift_assignments,
        "week_status": shift_week_status,
        "settings": shift_group_settings,
    }
    return {name: await db.scalar(select(func.count()).select_from(table)) for name, table in tables.items()}


@CHECKS
async def test_it_adds_the_demo_data_and_is_safe_to_run_twice(session) -> None:
    now = datetime.now(UTC)
    await _base(session, now)
    await _place_stjerne_people(session)
    semester = get_current_semester_code()
    first = await seed_into(session, now=now)
    after_first = await _counts(session)

    # About 100 people, including Halvtimen members born 2007 or later.
    assert after_first["volunteers"] == 100
    under_20 = (
        await session.execute(
            select(func.count())
            .select_from(
                volunteer_records.join(role_assignments, role_assignments.c.volunteer_id == volunteer_records.c.id)
                .join(groups, groups.c.id == role_assignments.c.group_id)
            )
            .where(groups.c.name == "Halvtimen", volunteer_records.c.birth_date >= date(2007, 1, 1))
        )
    ).scalar_one()
    assert under_20 >= 3
    emails = set((await session.execute(select(volunteer_records.c.email))).scalars())
    assert all(email.endswith("@example.dev") for email in emails)
    assert {"demo.stjerne@example.dev", "demo.under20@example.dev", "demo.samf@example.dev"} <= emails

    # Verv outside the Skjenke groups.
    outside = (
        await session.execute(
            select(groups.c.name)
            .select_from(
                role_assignments.join(volunteer_records, volunteer_records.c.id == role_assignments.c.volunteer_id)
                .join(groups, groups.c.id == role_assignments.c.group_id)
            )
            .where(volunteer_records.c.email == "demo.samf@example.dev", role_assignments.c.semester == semester)
        )
    ).scalars().all()
    assert outside == ["Lydgruppen"]

    # Courses: barkurs exists now, and some brannkurs has run out.
    bar = await session.scalar(select(courses.c.id).where(courses.c.name == "Barkurs"))
    brann = await session.scalar(select(courses.c.id).where(courses.c.name == "Brannkurs"))
    assert await session.scalar(
        select(func.count()).select_from(course_completions).where(course_completions.c.course_id == bar)
    )
    old = await session.scalar(
        select(func.min(course_completions.c.completed_semester)).where(course_completions.c.course_id == brann)
    )
    assert old < semester - 20  # more than four semesters back

    # Answers, wishes, and the marks.
    assert first["availability_rows"] > 50 and after_first["rules"] == first["availability_rows"]
    assert first["wishes"] > 0
    marked = (
        await session.execute(
            select(shift_assignments.c.status_message, shift_assignments.c.cannot_attend, shift_assignments.c.swap_requested)
            .where(shift_assignments.c.status_message.is_not(None))
        )
    ).all()
    assert (SWAP_MESSAGE, False, True) in [tuple(row) for row in marked]
    assert sum(1 for _, cannot, _ in marked if cannot) == 2

    # Grøndahls and Halvtimen are on in the app; courses are checked in Stjerne and Grøndahls.
    settings = {
        name: (app, courses_on)
        for name, app, courses_on in (
            await session.execute(
                select(groups.c.name, shift_group_settings.c.app_enabled, shift_group_settings.c.courses_enabled)
                .select_from(shift_group_settings.join(groups, groups.c.id == shift_group_settings.c.group_id))
            )
        ).all()
    }
    assert settings["Stjerne"] == (True, True)
    assert settings["Grøndahls"] == (True, True)
    assert settings["Halvtimen"][0] is True
    assert after_first["week_status"] >= 6  # this and next month for three groups

    # Running it again changes nothing.
    again = await seed_into(session, now=now)
    assert again == {"people": 0, "availability_rows": 0, "wishes": 0, "status_marks": 0}
    assert await _counts(session) == after_first
