"""Synthetic Skiftlista data for the local development database.

Adds Skjenke's subgroups (Stjerne, Kjøkken, Grøndahls, Halvtimen) under
Skjenkegruppen, their verv, made-up members for this semester, the H26
shift templates and six weeks of shifts from this week, partly filled.
Stjerne is switched on for the app (also for samfunnettjeneste), with
brann- and barkurs dates and brannkurs completions for most members.

Deterministic and entirely fake. Idempotent: does nothing when shift
templates already exist. Run by ``scripts/dev/bootstrap.py``.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import insert, select

from app.config import Settings
from app.db.session import build_database_runtime, session_scope
from app.db.tables import (
    assignment_roles,
    course_completions,
    course_sessions,
    courses,
    groups,
    role_assignments,
    shift_assignments,
    shift_group_settings,
    shift_month_status,
    shift_slots,
    shift_template_slots,
    shift_templates,
    shifts,
    volunteer_records,
)
from app.domain.shifts.app_rules import MONTH_PUBLISHED, add_months, month_start
from app.shared.semester import get_current_semester_code, get_previous_semester_code
from app.shared.slugs import slugify
from seed_synthetic import FIRST_NAMES, LAST_NAMES

OSLO = ZoneInfo("Europe/Oslo")
WEEKS = 6

# name, description, {verv: number of members}
SUBGROUPS: list[tuple[str, str, dict[str, int]]] = [
    (
        "Stjerne",
        "Stjernesalen: kafé og bar på dagtid og kveld",
        {"Skiftleder": 6, "Skjenker": 9, "Opplæring": 2, "Gruppeleder": 1},
    ),
    (
        "Kjøkken",
        "Kjøkkenet i Stjernesalen",
        {"Kokk": 6, "Opplæring": 1, "Gruppeleder": 1},
    ),
    (
        "Grøndahls",
        "Grøndahls pub",
        {"Skiftleder": 4, "Skjenker": 6, "Opplæring": 1, "Gruppeleder": 1},
    ),
    (
        "Halvtimen",
        "Cocktailbaren Halvtimen (20 år)",
        {"Skiftleder": 3, "Skjenker": 5, "Opplæring": 1, "Gruppeleder": 1},
    ),
]
POINTS = {"Skjenker": 2, "Kokk": 2, "Skiftleder": 4, "Opplæring": 1, "Gruppeleder": 6}


@dataclass(frozen=True)
class TemplateSpec:
    group: str
    venue: str
    title: str
    weekdays: tuple[int, ...]
    start: time
    end: time
    # kind, count, open for samfunnettjeneste
    slots: tuple[tuple[str, int, bool], ...]
    saturday_extra: tuple[tuple[str, int, bool], ...] = ()


DAY_SLOTS = (("skiftleder", 1, False), ("skjenker", 1, True), ("opplaering", 1, False))
TEMPLATES = [
    TemplateSpec("Stjerne", "Stjernesalen", "Dagskift", (0, 1, 2, 3, 4), time(11, 30), time(17, 0), DAY_SLOTS),
    TemplateSpec("Stjerne", "Stjernesalen", "Kveldsskift", (0, 2, 3, 4), time(16, 30), time(22, 0), DAY_SLOTS),
    TemplateSpec(
        "Stjerne",
        "Stjernesalen",
        "Quizskift",
        (1,),
        time(16, 30),
        time(23, 0),
        (("skiftleder", 1, False), ("skjenker", 2, True), ("opplaering", 1, False)),
    ),
    TemplateSpec(
        "Kjøkken",
        "Stjernesalen",
        "Kjøkken",
        (0, 1, 2, 3, 4),
        time(14, 0),
        time(20, 0),
        (("kokk", 2, False), ("opplaering", 1, False)),
    ),
    TemplateSpec("Grøndahls", "Grøndahls", "Pub", (0, 1, 2, 3), time(19, 30), time(1, 30), DAY_SLOTS),
    TemplateSpec(
        "Grøndahls",
        "Grøndahls",
        "Pub",
        (4, 5),
        time(20, 30),
        time(3, 30),
        DAY_SLOTS,
        saturday_extra=(("garderobe", 1, True),),
    ),
    TemplateSpec(
        "Halvtimen",
        "Halvtimen",
        "Åpningsskift",
        (4, 5),
        time(20, 30),
        time(3, 30),
        (("skiftleder", 1, False), ("bartender", 2, True), ("opplaering", 1, False)),
    ),
]


def _span(day: date, start: time, end: time) -> tuple[datetime, datetime]:
    starts_at = datetime.combine(day, start, tzinfo=OSLO)
    end_day = day + timedelta(days=1) if end <= start else day
    ends_at = datetime.combine(end_day, end, tzinfo=OSLO)
    return starts_at.astimezone(UTC), ends_at.astimezone(UTC)


async def seed_shifts(database_url: str) -> None:
    rng = random.Random(2026)
    settings = Settings(database_url=database_url, app_env="development")
    runtime = build_database_runtime(settings)
    now = datetime.now(UTC)
    semester = get_current_semester_code()
    today = datetime.now(OSLO).date()
    first_monday = today - timedelta(days=today.weekday())

    try:
        async with session_scope(runtime) as session:
            if await session.scalar(select(shift_templates.c.id).limit(1)) is not None:
                print("Shift data already seeded; skipping.")
                return
            parent_id = await session.scalar(
                select(groups.c.id).where(groups.c.name == "Skjenkegruppen")
            )

            # Subgroups, verv and members.
            group_ids: dict[str, int] = {}
            members: dict[str, dict[str, list[int]]] = {}
            index = 0
            for name, description, verv in SUBGROUPS:
                group_id = (
                    await session.execute(
                        insert(groups)
                        .values(
                            slug=slugify(name),
                            name=name,
                            description=description,
                            is_active=True,
                            active_through_semester=20991,
                            parent_group_id=parent_id,
                            discount_tier=2,
                            created_at=now,
                        )
                        .returning(groups.c.id)
                    )
                ).scalar_one()
                group_ids[name] = group_id
                members[name] = {}
                for role_name, count in verv.items():
                    role_id = (
                        await session.execute(
                            insert(assignment_roles)
                            .values(group_id=group_id, name=role_name, penguin_points=POINTS[role_name])
                            .returning(assignment_roles.c.id)
                        )
                    ).scalar_one()
                    members[name][role_name] = []
                    for _ in range(count):
                        index += 1
                        first = rng.choice(FIRST_NAMES)
                        last = rng.choice(LAST_NAMES)
                        oldest, youngest = (1996, 2005) if name == "Halvtimen" else (1996, 2007)
                        volunteer_id = (
                            await session.execute(
                                insert(volunteer_records)
                                .values(
                                    first_name=first,
                                    last_name=last,
                                    email=f"{first}.{last}.skift{index}@example.dev".lower(),
                                    phone="4" + "".join(rng.choices("0123456789", k=7)),
                                    birth_date=date(
                                        rng.randint(oldest, youngest), rng.randint(1, 12), rng.randint(1, 28)
                                    ),
                                    gender=rng.choice(["M", "K", "A"]),
                                    created_at=now - timedelta(days=rng.randint(30, 900)),
                                )
                                .returning(volunteer_records.c.id)
                            )
                        ).scalar_one()
                        await session.execute(
                            insert(role_assignments).values(
                                volunteer_id=volunteer_id,
                                group_id=group_id,
                                role_id=role_id,
                                semester=semester,
                                contract_signed=rng.random() > 0.1,
                            )
                        )
                        members[name][role_name].append(volunteer_id)

            # Templates.
            template_ids: dict[tuple[str, str, int], int] = {}
            template_slots: dict[int, list[tuple[str, int, bool]]] = {}
            template_specs: dict[int, TemplateSpec] = {}
            for spec in TEMPLATES:
                for weekday in spec.weekdays:
                    slots = list(spec.slots) + (list(spec.saturday_extra) if weekday == 5 else [])
                    template_id = (
                        await session.execute(
                            insert(shift_templates)
                            .values(
                                group_id=group_ids[spec.group],
                                venue=spec.venue,
                                title=spec.title,
                                weekday=weekday,
                                start_time=spec.start,
                                end_time=spec.end,
                                training_replaces_slot=True,
                                is_active=True,
                            )
                            .returning(shift_templates.c.id)
                        )
                    ).scalar_one()
                    await session.execute(
                        insert(shift_template_slots),
                        [
                            {
                                "template_id": template_id,
                                "kind": kind,
                                "count": count,
                                "open_for_samf": open_for_samf,
                                "sort_order": order,
                            }
                            for order, (kind, count, open_for_samf) in enumerate(slots)
                        ],
                    )
                    template_ids[(spec.group, spec.title, weekday)] = template_id
                    template_slots[template_id] = slots
                    template_specs[template_id] = spec

            # Shifts for the coming weeks, filled the way a leader might:
            # skiftleder first, sometimes a trainee (who takes a regular
            # place), then the rest of the crew. One shift a week each.
            shift_count = 0
            swap_marked = False
            for week in range(WEEKS):
                monday = first_monday + timedelta(days=7 * week)
                busy: set[int] = set()
                for day_offset in range(7):
                    day = monday + timedelta(days=day_offset)
                    for (group_name, title, weekday), template_id in template_ids.items():
                        if weekday != day.weekday():
                            continue
                        spec = template_specs[template_id]
                        starts_at, ends_at = _span(day, spec.start, spec.end)
                        closed = group_name == "Halvtimen" and week == 1 and weekday == 5
                        note = None
                        if title == "Quizskift":
                            note = "Reservert bord 29 og 30 fra 18 (kun quizmeny)"
                        shift_id = (
                            await session.execute(
                                insert(shifts)
                                .values(
                                    group_id=group_ids[group_name],
                                    template_id=template_id,
                                    venue=spec.venue,
                                    title=title,
                                    starts_at=starts_at,
                                    ends_at=ends_at,
                                    note=note,
                                    is_closed=closed,
                                    training_replaces_slot=True,
                                )
                                .returning(shifts.c.id)
                            )
                        ).scalar_one()
                        shift_count += 1
                        slot_ids: dict[str, tuple[int, int]] = {}
                        for order, (kind, count, open_for_samf) in enumerate(template_slots[template_id]):
                            slot_id = (
                                await session.execute(
                                    insert(shift_slots)
                                    .values(
                                        shift_id=shift_id,
                                        kind=kind,
                                        count=count,
                                        open_for_samf=open_for_samf,
                                        sort_order=order,
                                    )
                                    .returning(shift_slots.c.id)
                                )
                            ).scalar_one()
                            slot_ids[kind] = (slot_id, count)
                        if closed:
                            continue

                        pool = members[group_name]

                        def pick(candidates: list[int]) -> int | None:
                            free = [person for person in candidates if person not in busy]
                            if not free or rng.random() < 0.12:
                                return None
                            person = rng.choice(free)
                            busy.add(person)
                            return person

                        placed: list[tuple[int, int]] = []
                        if "skiftleder" in slot_ids:
                            person = pick(pool.get("Skiftleder", []))
                            if person is not None:
                                placed.append((slot_ids["skiftleder"][0], person))
                        crew_kind = next(
                            (kind for kind in ("skjenker", "bartender", "kokk") if kind in slot_ids), None
                        )
                        crew_places = slot_ids[crew_kind][1] if crew_kind else 0
                        if "opplaering" in slot_ids and rng.random() < 0.35:
                            person = pick(pool.get("Opplæring", []))
                            if person is not None:
                                placed.append((slot_ids["opplaering"][0], person))
                                crew_places -= 1
                        if crew_kind:
                            crew_pool = pool.get("Kokk" if crew_kind == "kokk" else "Skjenker", [])
                            for _ in range(max(0, crew_places)):
                                person = pick(crew_pool)
                                if person is not None:
                                    placed.append((slot_ids[crew_kind][0], person))
                        for slot_id, volunteer_id in placed:
                            await session.execute(
                                insert(shift_assignments).values(
                                    slot_id=slot_id,
                                    volunteer_id=volunteer_id,
                                    swap_requested=not swap_marked and week == 0 and day.weekday() == 5,
                                )
                            )
                            if week == 0 and day.weekday() == 5:
                                swap_marked = True

            # The app: Stjerne is the pilot, also open for samfunnettjeneste.
            await session.execute(
                insert(shift_group_settings).values(
                    group_id=group_ids["Stjerne"],
                    app_enabled=True,
                    samf_enabled=True,
                    meeting_point="Møt skiftleder i Stjernesalen 30 minutter før.",
                )
            )
            # Next month is allocated by the 20th, so it is published. Later
            # months have no row and stay open for wishes.
            for group_name in ("Stjerne",):
                await session.execute(
                    insert(shift_month_status).values(
                        group_id=group_ids[group_name],
                        month=add_months(month_start(today), 1),
                        status=MONTH_PUBLISHED,
                    )
                )

            # Brann- and barkurs with a few coming dates, and completions for
            # most Skjenke members (some missing, so the app shows sign-up).
            course_ids: dict[str, int] = {}
            for key, names in (("brann", ("Brannkurs", "Brannvernkurs")), ("bar", ("Barkurs",))):
                existing = await session.scalar(select(courses.c.id).where(courses.c.name.in_(names)))
                course_ids[key] = existing or (
                    await session.execute(
                        insert(courses).values(name=names[0], created_at=now).returning(courses.c.id)
                    )
                ).scalar_one()
            for key, offsets, capacity in (("brann", (8, 22, 36), 16), ("bar", (15, 29), 12)):
                for offset in offsets:
                    starts_at, _ = _span(today + timedelta(days=offset), time(18, 0), time(20, 0))
                    await session.execute(
                        insert(course_sessions).values(
                            course_id=course_ids[key],
                            starts_at=starts_at,
                            location="Kvarteret",
                            capacity=capacity,
                            is_cancelled=False,
                        )
                    )
            previous = get_previous_semester_code(semester)
            for name in members:
                for role_name, people in members[name].items():
                    for volunteer_id in people:
                        if role_name != "Opplæring" and rng.random() < 0.15:
                            continue
                        await session.execute(
                            insert(course_completions).values(
                                volunteer_id=volunteer_id,
                                course_id=course_ids["brann"],
                                completed_semester=rng.choice([semester, previous]),
                                created_at=now,
                            )
                        )

            print(
                f"Seeded {len(SUBGROUPS)} Skjenke subgroups, {index} shift volunteers, "
                f"{len(template_ids)} shift templates and {shift_count} shifts."
            )
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    url = os.environ.get(
        "DEV_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:55432/kvarteret_personal_dev",
    )
    asyncio.run(seed_shifts(url))
