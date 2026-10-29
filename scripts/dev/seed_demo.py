"""Demo data for showing Skiftlista in a meeting. Entirely fake.

Adds to a database that ``seed_synthetic`` and ``seed_shifts`` have already filled:
about 100 volunteers in total, Halvtimen members under 20, barkurs completions and a few
expired brannkurs, weekday answers and shift wishes, a "kan ikke" and a swap request in
the coming two weeks, Grøndahls and Halvtimen switched on in the app (with their weeks
published), qualification rows for the Skjenke members, and a few people with verv outside
the Skjenke groups (no rows: they only see places open for samfunnettjeneste).

Safe to run twice: everything that exists is skipped. Deterministic.

It refuses to run unless the database host is localhost / 127.0.0.1, or
``DEMO_SEED_CONFIRM`` is set to exactly that host name, and it refuses when any volunteer
has an email that does not end in ``@example.dev``, so it can never touch real people.

    uv run python scripts/dev/seed_demo.py
    DEMO_SEED_CONFIRM=<host> DATABASE_URL=<url> uv run python scripts/dev/seed_demo.py
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db.session import build_database_runtime, session_scope
from app.db.tables import (
    assignment_roles,
    course_completions,
    courses,
    groups,
    role_assignments,
    shift_assignments,
    shift_availability_rules,
    shift_group_settings,
    shift_slots,
    shift_week_status,
    shift_wishes,
    shifts,
    volunteer_records,
)
from app.domain.shifts.app_rules import (
    COURSE_VALID_SEMESTERS,
    STATUS_PUBLISHED,
    add_months,
    effective_week_status,
    semester_from_index,
    semester_index,
    week_start,
)
from app.shared.semester import get_current_semester_code, get_previous_semester_code
from seed_shifts import add_qualifications
from seed_synthetic import FIRST_NAMES, LAST_NAMES

OSLO = ZoneInfo("Europe/Oslo")
FAKE_DOMAIN = "@example.dev"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
DEFAULT_URL = "postgresql+asyncpg://postgres:postgres@localhost:55432/kvarteret_personal_dev"
TARGET_TOTAL = 100
SKJENKE_GROUPS = ("Stjerne", "Kjøkken", "Grøndahls", "Halvtimen")
BRANN_NAMES = ("Brannkurs", "Brannvernkurs")
BAR_NAME = "Barkurs"
CANNOT_ATTEND_MESSAGES = ("Syk i dag, kan ikke komme.", "Eksamen i dag.")
SWAP_MESSAGE = "Har eksamen, trenger bytte."
ANSWERS = ("vil", "kan", "nei")


class DemoSeedRefused(RuntimeError):
    """A safety check failed; nothing was written."""


# ------------------------------------------------------------------- safety


def database_host(database_url: str) -> str | None:
    return make_url(database_url).host


def check_database_target(database_url: str, confirm: str | None) -> str:
    """The host to seed, or a refusal. A hosted database needs DEMO_SEED_CONFIRM=<host>."""
    host = database_host(database_url)
    if not host:
        raise DemoSeedRefused("DATABASE_URL has no host name, so it cannot be confirmed.")
    if host in LOCAL_HOSTS:
        return host
    if confirm != host:
        raise DemoSeedRefused(
            f"{host} is not local. To seed this database on purpose, set DEMO_SEED_CONFIRM={host}."
        )
    return host


async def ensure_only_fake_people(session) -> None:
    """Refuse when any volunteer could be a real person: no email, or not @example.dev."""
    email = func.lower(volunteer_records.c.email)
    count = await session.scalar(
        select(func.count())
        .select_from(volunteer_records)
        .where(or_(volunteer_records.c.email.is_(None), ~email.like(f"%{FAKE_DOMAIN}")))
    )
    if count:
        raise DemoSeedRefused(
            f"{count} volunteer(s) have no email or an email that does not end in {FAKE_DOMAIN}. "
            "This script only runs on fake data."
        )


# --------------------------------------------------------------------- data


@dataclass(frozen=True)
class DemoPerson:
    email: str
    first: str
    last: str
    group: str
    role: str
    born: date
    brann: str = "valid"  # valid | expired
    bar: bool = True


DEMO_PEOPLE = (
    # Own group (Stjerne), valid courses: claims a place and sees the "Andre" tab.
    DemoPerson("demo.stjerne@example.dev", "Mats", "Solberg", "Stjerne", "Skjenker", date(1999, 4, 12)),
    DemoPerson("demo.skiftleder@example.dev", "Hedda", "Dahl", "Stjerne", "Skiftleder", date(1997, 9, 3)),
    DemoPerson("demo.opplaering@example.dev", "Ylva", "Vik", "Stjerne", "Opplæring", date(2005, 2, 20)),
    # Brannkurs has run out and no barkurs: the app offers a course sign-up.
    DemoPerson(
        "demo.mangler-kurs@example.dev", "Iver", "Berg", "Stjerne", "Skjenker", date(2001, 6, 8),
        brann="expired", bar=False,
    ),
    # Has a swap request in the coming weeks.
    DemoPerson("demo.bytte@example.dev", "Live", "Haugen", "Stjerne", "Skjenker", date(2002, 11, 25)),
    # Has a "kan ikke" in the coming weeks.
    DemoPerson("demo.kan-ikke@example.dev", "Didrik", "Lie", "Grøndahls", "Skjenker", date(2000, 1, 17)),
    DemoPerson("demo.kokk@example.dev", "Frida", "Moen", "Kjøkken", "Kokk", date(1998, 7, 30)),
    # Under 20: Halvtimen is blocked ("20+").
    DemoPerson("demo.under20@example.dev", "Oskar", "Fjeld", "Halvtimen", "Skjenker", date(2007, 8, 14)),
    DemoPerson("demo.under20-b@example.dev", "Tuva", "Knutsen", "Halvtimen", "Skjenker", date(2008, 3, 5)),
    DemoPerson("demo.under20-c@example.dev", "Ronja", "Tangen", "Halvtimen", "Skjenker", date(2007, 12, 1)),
    DemoPerson("demo.halvtimen@example.dev", "Kasper", "Nilsen", "Halvtimen", "Skjenker", date(2000, 5, 9)),
    # Verv outside the Skjenke groups: samfunnettjeneste on open places.
    DemoPerson("demo.samf@example.dev", "Nora", "Eriksen", "Lydgruppen", "Frivillig", date(2000, 10, 2)),
    DemoPerson("demo.samf-b@example.dev", "William", "Gundersen", "Eksponeringsetaten", "Frivillig", date(2003, 4, 21)),
)
FILLER_PLAN = (
    ("Stjerne", "Skjenker"),
    ("Grøndahls", "Skjenker"),
    ("Kjøkken", "Kokk"),
    ("Halvtimen", "Skjenker"),
    ("Stjerne", "Skjenker"),
    ("Grøndahls", "Skjenker"),
)


async def _group_ids(session) -> dict[str, int]:
    rows = (await session.execute(select(groups.c.name, groups.c.id))).all()
    return {name: group_id for name, group_id in rows}


async def _role_ids(session) -> dict[tuple[str, str], int]:
    rows = (
        await session.execute(
            select(groups.c.name, assignment_roles.c.name, assignment_roles.c.id).select_from(
                assignment_roles.join(groups, groups.c.id == assignment_roles.c.group_id)
            )
        )
    ).all()
    return {(group, role): role_id for group, role, role_id in rows}


async def _course_id(session, names: tuple[str, ...], now: datetime) -> int:
    found = await session.scalar(select(courses.c.id).where(courses.c.name.in_(names)))
    if found is not None:
        return found
    return (
        await session.execute(insert(courses).values(name=names[0], created_at=now).returning(courses.c.id))
    ).scalar_one()


async def _add_person(
    session, *, email: str, first: str, last: str, born: date, group_id: int, role_id: int,
    semester: int, now: datetime, rng: random.Random,
) -> bool:
    if await session.scalar(select(volunteer_records.c.id).where(func.lower(volunteer_records.c.email) == email)):
        return False
    volunteer_id = (
        await session.execute(
            insert(volunteer_records)
            .values(
                first_name=first,
                last_name=last,
                email=email,
                phone="4" + "".join(rng.choices("0123456789", k=7)),
                birth_date=born,
                gender=rng.choice(["M", "K", "A"]),
                created_at=now - timedelta(days=rng.randint(30, 900)),
            )
            .returning(volunteer_records.c.id)
        )
    ).scalar_one()
    await session.execute(
        insert(role_assignments).values(
            volunteer_id=volunteer_id, group_id=group_id, role_id=role_id, semester=semester, contract_signed=True
        )
    )
    return True


async def add_people(session, *, now: datetime, semester: int) -> int:
    """The fixed demo people, then fillers until there are about TARGET_TOTAL volunteers."""
    rng = random.Random(7001)
    group_ids = await _group_ids(session)
    role_ids = await _role_ids(session)
    missing = [n for n in {p.group for p in DEMO_PEOPLE} | set(SKJENKE_GROUPS) if n not in group_ids]
    if missing:
        raise DemoSeedRefused(f"Missing groups {sorted(missing)}. Run seed_synthetic and seed_shifts first.")

    created = 0
    for person in DEMO_PEOPLE:
        role_id = role_ids.get((person.group, person.role))
        if role_id is None:
            raise DemoSeedRefused(f"No verv {person.role} in {person.group}. Run seed_shifts first.")
        created += await _add_person(
            session, email=person.email, first=person.first, last=person.last, born=person.born,
            group_id=group_ids[person.group], role_id=role_id, semester=semester, now=now, rng=rng,
        )

    total = await session.scalar(select(func.count()).select_from(volunteer_records))
    number = 0
    while total < TARGET_TOTAL and number < TARGET_TOTAL * 3:
        group, role = FILLER_PLAN[number % len(FILLER_PLAN)]
        number += 1
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        oldest, youngest = (1996, 2005) if group == "Halvtimen" else (1996, 2007)
        born = date(rng.randint(oldest, youngest), rng.randint(1, 12), rng.randint(1, 28))
        email = f"{first}.{last}.demo{number}{FAKE_DOMAIN}".lower()
        added = await _add_person(
            session, email=email, first=first, last=last, born=born, group_id=group_ids[group],
            role_id=role_ids[(group, role)], semester=semester, now=now, rng=rng,
        )
        created += added
        total += added
    return created


async def _members(session, semester: int) -> list[tuple[int, str, str]]:
    """(volunteer id, group, verv) for this semester's members of the Skjenke groups."""
    rows = (
        await session.execute(
            select(role_assignments.c.volunteer_id, groups.c.name, assignment_roles.c.name)
            .select_from(
                role_assignments.join(groups, groups.c.id == role_assignments.c.group_id).join(
                    assignment_roles, assignment_roles.c.id == role_assignments.c.role_id
                )
            )
            .where(role_assignments.c.semester == semester, groups.c.name.in_(SKJENKE_GROUPS))
            .order_by(role_assignments.c.volunteer_id)
        )
    ).all()
    return [(vid, group, role) for vid, group, role in rows]


async def add_courses(session, *, now: datetime, semester: int, members: list[tuple[int, str, str]]) -> None:
    brann = await _course_id(session, BRANN_NAMES, now)
    bar = await _course_id(session, (BAR_NAME,), now)
    previous = get_previous_semester_code(semester)
    expired = semester_from_index(semester_index(semester) - COURSE_VALID_SEMESTERS - 1)
    by_email = {
        email: vid
        for email, vid in (
            await session.execute(select(volunteer_records.c.email, volunteer_records.c.id))
        ).all()
    }
    demo = {p.email: p for p in DEMO_PEOPLE}

    async def has(vid: int, course_id: int) -> bool:
        return (
            await session.scalar(
                select(course_completions.c.id).where(
                    course_completions.c.volunteer_id == vid, course_completions.c.course_id == course_id
                )
            )
        ) is not None

    async def give(vid: int, course_id: int, completed: int) -> None:
        await session.execute(
            insert(course_completions).values(
                volunteer_id=vid, course_id=course_id, completed_semester=completed, created_at=now
            )
        )

    # A few existing members whose brannkurs ran out, picked by id so reruns pick the same.
    plain = [vid for vid, _, role in members if role != "Opplæring" and by_email_is_plain(vid, by_email, demo)]
    expiring = {vid for index, vid in enumerate(plain) if index % 9 == 4}
    for person in demo.values():
        vid = by_email.get(person.email)
        if vid is None:
            continue
        if person.brann == "expired":
            expiring.add(vid)
    for vid in sorted(expiring):
        await session.execute(
            delete(course_completions).where(
                course_completions.c.volunteer_id == vid, course_completions.c.course_id == brann
            )
        )
        await give(vid, brann, expired)

    for person in demo.values():
        vid = by_email.get(person.email)
        if vid is None:
            continue
        if person.brann == "valid" and not await has(vid, brann):
            await give(vid, brann, semester)
        if person.bar and not await has(vid, bar):
            await give(vid, bar, semester)

    # Barkurs for about half of the other members.
    for index, vid in enumerate(sorted({vid for vid, _, _ in members})):
        if index % 2 == 0 and vid not in expiring and not await has(vid, bar):
            await give(vid, bar, previous if index % 4 == 0 else semester)


def by_email_is_plain(vid: int, by_email: Mapping[str, int], demo: Mapping[str, DemoPerson]) -> bool:
    """Not one of the fixed demo people, whose courses are set on purpose."""
    return all(by_email.get(email) != vid for email in demo)


async def add_availability(session, *, semester: int, members: list[tuple[int, str, str]]) -> int:
    already = set(
        (
            await session.execute(
                select(shift_availability_rules.c.volunteer_id).where(
                    shift_availability_rules.c.semester == semester
                )
            )
        ).scalars()
    )
    rows = []
    for vid in sorted({vid for vid, _, _ in members}):
        rng = random.Random(f"availability-{vid}")
        if vid in already or rng.random() < 0.15:  # most, not all
            continue
        for weekday in sorted(rng.sample(range(7), rng.randint(3, 5))):
            answer = rng.choices(ANSWERS, weights=(4, 3.5, 2.5))[0]
            rows.append(
                {"volunteer_id": vid, "semester": semester, "weekday": weekday, "answer": answer}
            )
    if rows:
        await session.execute(insert(shift_availability_rules), rows)
    return len(rows)


async def add_wishes(session, *, today: date, members: list[tuple[int, str, str]]) -> int:
    """Per-shift answers on shifts in weeks that are still open for wishes."""
    first_monday = week_start(today) + timedelta(days=7)
    starts_from = datetime.combine(first_monday, datetime.min.time(), tzinfo=OSLO)
    upcoming = (
        await session.execute(
            select(shifts.c.id, shifts.c.group_id, shifts.c.starts_at)
            .where(shifts.c.starts_at >= starts_from, shifts.c.is_closed.is_(False))
            .order_by(shifts.c.starts_at, shifts.c.id)
            .limit(400)
        )
    ).all()
    explicit = {
        (group_id, _as_date(week)): status
        for group_id, week, status in (
            await session.execute(
                select(shift_week_status.c.group_id, shift_week_status.c.week_start, shift_week_status.c.status)
            )
        ).all()
    }
    by_group: dict[int, list[int]] = {}
    for shift_id, group_id, starts_at in upcoming:
        week = week_start(_aware(starts_at).astimezone(OSLO).date())
        if effective_week_status(explicit.get((group_id, week)), week, today) != STATUS_PUBLISHED:
            by_group.setdefault(group_id, []).append(shift_id)

    group_ids = await _group_ids(session)
    existing = {
        (shift_id, vid)
        for shift_id, vid in (await session.execute(select(shift_wishes.c.shift_id, shift_wishes.c.volunteer_id))).all()
    }
    rows = []
    for vid, group, _ in members:
        rng = random.Random(f"wishes-{vid}")
        pool = by_group.get(group_ids[group], [])
        if not pool or rng.random() > 0.45:
            continue
        for shift_id in rng.sample(pool, min(len(pool), rng.randint(1, 3))):
            if (shift_id, vid) not in existing:
                existing.add((shift_id, vid))
                rows.append({"shift_id": shift_id, "volunteer_id": vid, "answer": rng.choice(ANSWERS)})
    if rows:
        await session.execute(insert(shift_wishes), rows)
    return len(rows)


async def _free_place(session, *, group_id: int, kind: str, start: datetime, end: datetime, volunteer_id: int):
    """A slot of ``kind`` with room on an open shift of the group in the window, or None."""
    candidates = (
        await session.execute(
            select(shifts.c.id, shift_slots.c.id, shift_slots.c.count)
            .select_from(shifts.join(shift_slots, shift_slots.c.shift_id == shifts.c.id))
            .where(
                shifts.c.group_id == group_id,
                shift_slots.c.kind == kind,
                shifts.c.is_closed.is_(False),
                shifts.c.starts_at >= start,
                shifts.c.starts_at < end,
            )
            .order_by(shifts.c.starts_at, shift_slots.c.id)
        )
    ).all()
    for shift_id, slot_id, count in candidates:
        filled = await session.scalar(
            select(func.count()).select_from(shift_assignments).where(shift_assignments.c.slot_id == slot_id)
        )
        on_shift = await session.scalar(
            select(func.count())
            .select_from(
                shift_assignments.join(shift_slots, shift_slots.c.id == shift_assignments.c.slot_id)
            )
            .where(shift_slots.c.shift_id == shift_id, shift_assignments.c.volunteer_id == volunteer_id)
        )
        if filled < count and not on_shift:
            return slot_id
    return None


async def add_status_marks(session, *, now: datetime) -> list[str]:
    """Two "kan ikke" and one more swap request in the coming two weeks."""
    done: list[str] = []
    start, end = now, now + timedelta(days=14)
    group_ids = await _group_ids(session)
    by_email = {
        email: vid
        for email, vid in (await session.execute(select(volunteer_records.c.email, volunteer_records.c.id))).all()
    }

    async def marked(message: str) -> bool:
        return (
            await session.scalar(
                select(shift_assignments.c.id).where(shift_assignments.c.status_message == message)
            )
        ) is not None

    async def place_and_mark(email: str, group: str, message: str, *, swap: bool) -> None:
        vid = by_email.get(email)
        if vid is None or await marked(message):
            return
        slot_id = await _free_place(
            session, group_id=group_ids[group], kind="skjenker", start=start, end=end, volunteer_id=vid
        )
        if slot_id is None:
            return
        await session.execute(
            insert(shift_assignments).values(
                slot_id=slot_id,
                volunteer_id=vid,
                swap_requested=swap,
                cannot_attend=not swap,
                status_message=message,
                status_changed_at=now,
            )
        )
        done.append(message)

    await place_and_mark("demo.kan-ikke@example.dev", "Grøndahls", CANNOT_ATTEND_MESSAGES[0], swap=False)
    await place_and_mark("demo.bytte@example.dev", "Stjerne", SWAP_MESSAGE, swap=True)

    # One more "kan ikke" on an existing placement in the window.
    if not await marked(CANNOT_ATTEND_MESSAGES[1]):
        plain = (
            await session.execute(
                select(shift_assignments.c.id)
                .select_from(
                    shift_assignments.join(shift_slots, shift_slots.c.id == shift_assignments.c.slot_id).join(
                        shifts, shifts.c.id == shift_slots.c.shift_id
                    )
                )
                .where(
                    shifts.c.group_id == group_ids["Stjerne"],
                    shifts.c.starts_at >= start,
                    shifts.c.starts_at < end,
                    shift_assignments.c.cannot_attend.is_(False),
                    shift_assignments.c.swap_requested.is_(False),
                    shift_assignments.c.status_message.is_(None),
                    shift_slots.c.kind == "skjenker",
                )
                .order_by(shifts.c.starts_at, shift_assignments.c.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if plain is not None:
            await session.execute(
                update(shift_assignments)
                .where(shift_assignments.c.id == plain)
                .values(cannot_attend=True, status_message=CANNOT_ATTEND_MESSAGES[1], status_changed_at=now)
            )
            done.append(CANNOT_ATTEND_MESSAGES[1])
    return done


async def switch_groups_on(session, *, today: date) -> None:
    """Stjerne and Grøndahls with course checks; Halvtimen so the 20+ block can be shown."""
    group_ids = await _group_ids(session)
    plan = {
        "Stjerne": dict(app_enabled=True, samf_enabled=True, courses_enabled=True, meeting_point=None),
        "Grøndahls": dict(
            app_enabled=True,
            samf_enabled=True,
            courses_enabled=True,
            meeting_point="Møt skiftleder i Grøndahls 30 minutter før.",
        ),
        "Halvtimen": dict(app_enabled=True, samf_enabled=False, courses_enabled=False, meeting_point=None),
    }
    for name, values in plan.items():
        group_id = group_ids[name]
        exists = await session.scalar(
            select(shift_group_settings.c.group_id).where(shift_group_settings.c.group_id == group_id)
        )
        if exists is None:
            await session.execute(insert(shift_group_settings).values(group_id=group_id, **values))
        elif name != "Halvtimen":
            # Only ever turn the demo switches on; leave the meeting point and samf as they are.
            await session.execute(
                update(shift_group_settings)
                .where(shift_group_settings.c.group_id == group_id)
                .values(app_enabled=True, courses_enabled=True)
            )
        # This and next month are published, like Stjerne; later weeks stay open for wishes.
        this_month = today.replace(day=1)
        monday = this_month + timedelta(days=(7 - this_month.weekday()) % 7)
        after_next = add_months(this_month, 2)
        while monday < after_next:
            has_row = await session.scalar(
                select(shift_week_status.c.group_id).where(
                    shift_week_status.c.group_id == group_id, shift_week_status.c.week_start == monday
                )
            )
            if has_row is None:
                await session.execute(
                    insert(shift_week_status).values(
                        group_id=group_id, week_start=monday, status=STATUS_PUBLISHED
                    )
                )
            monday += timedelta(days=7)


def _as_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------- run


async def seed_into(session, *, now: datetime) -> dict[str, int]:
    """Everything, in one session. The caller has already passed the safety checks."""
    await ensure_only_fake_people(session)
    semester = get_current_semester_code()
    today = now.astimezone(OSLO).date()
    shifts_exist = await session.scalar(select(shifts.c.id).limit(1))
    if shifts_exist is None:
        raise DemoSeedRefused("No shifts found. Run seed_synthetic and seed_shifts first.")

    summary = {"people": await add_people(session, now=now, semester=semester)}
    members = await _members(session, semester)
    group_ids = await _group_ids(session)
    summary["qualification_rows"] = await add_qualifications(
        session,
        [(vid, group_ids[group], group, role) for vid, group, role in members],
        now=now,
    )
    await add_courses(session, now=now, semester=semester, members=members)
    summary["availability_rows"] = await add_availability(session, semester=semester, members=members)
    await switch_groups_on(session, today=today)
    summary["wishes"] = await add_wishes(session, today=today, members=members)
    summary["status_marks"] = len(await add_status_marks(session, now=now))
    return summary


async def seed_demo(database_url: str, env: Mapping[str, str] | None = None) -> dict[str, int]:
    env = os.environ if env is None else env
    host = check_database_target(database_url, env.get("DEMO_SEED_CONFIRM"))
    runtime = build_database_runtime(Settings(database_url=database_url, app_env="development"))
    try:
        async with session_scope(runtime) as session:
            summary = await seed_into(session, now=datetime.now(UTC))
    finally:
        await runtime.aclose()
    print(f"Demo data added to {host}: {summary}")
    return summary


if __name__ == "__main__":
    url = os.environ.get("DATABASE_URL") or DEFAULT_URL
    try:
        asyncio.run(seed_demo(url))
    except DemoSeedRefused as refusal:
        raise SystemExit(f"Refused: {refusal}") from None
