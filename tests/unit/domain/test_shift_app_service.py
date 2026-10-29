"""Skiftlista in the app, against a real (SQLite) database.

Made-up people only. The clock is fixed to Monday 2 November 2026,
09:00 in Oslo (week 45, autumn semester 2026).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import BigInteger, Integer, delete, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.metadata import public_metadata
from app.db.session import reset_request_session, set_request_session
from app.domain.courses.tables import (
    course_completions,
    course_session_signups,
    course_sessions,
    courses,
)
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.app_models import ClaimPlaceRequest
from app.domain.shifts.app_rules import PERIOD_STATUSES
from app.domain.shifts.app_repository import ShiftsAppRepository
from app.domain.shifts.app_service import ShiftsAppService
from app.domain.shifts.errors import ShiftAccessError, ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.tables import (
    shift_assignments,
    shift_availability_rules,
    shift_group_settings,
    shift_week_status,
    shift_slots,
    shift_template_slots,
    shift_templates,
    shift_wishes,
    shifts,
    volunteer_qualifications,
)
from app.domain.volunteer_applications.tables import volunteer_application_invites
from app.domain.volunteers.repository import VolunteersRepository
from app.domain.volunteers.tables import (
    volunteer_cards,
    volunteer_next_of_kin,
    volunteer_photos,
    volunteer_records,
)

OSLO = ZoneInfo("Europe/Oslo")
SEMESTER = 20262

STJERNE, HALVTIMEN, KULTUR = 1, 2, 3
SIGRID, SANDER, TORA, OLA, UNGE, NOBODY = 10, 11, 12, 13, 14, 15
BRANN, BAR = 1, 2


def oslo(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=OSLO).astimezone(UTC)


class Clock:
    def __init__(self) -> None:
        self.now = oslo(date(2026, 11, 2), 9)

    def __call__(self) -> datetime:
        return self.now


TABLES = [
    volunteer_records,
    groups,
    assignment_roles,
    role_assignments,
    courses,
    course_completions,
    course_sessions,
    course_session_signups,
    shift_templates,
    shift_template_slots,
    shifts,
    shift_slots,
    shift_assignments,
    shift_group_settings,
    shift_week_status,
    shift_availability_rules,
    shift_wishes,
    volunteer_qualifications,
    volunteer_cards,
    volunteer_next_of_kin,
    volunteer_photos,
    volunteer_application_invites,
]


class World:
    """Ids of what the fixture created."""

    def __init__(self, session, clock: Clock) -> None:
        self.session = session
        self.clock = clock
        self.service = ShiftsAppService(ShiftsAppRepository(), now=clock)
        self.slots: dict[tuple[str, str], int] = {}
        self.shift_ids: dict[str, int] = {}
        self.sessions: dict[str, int] = {}

    async def place(self, shift: str, kind: str, volunteer_id: int) -> int:
        result = await self.session.execute(
            insert(shift_assignments)
            .values(slot_id=self.slots[(shift, kind)], volunteer_id=volunteer_id, swap_requested=False)
            .returning(shift_assignments.c.id)
        )
        return result.scalar_one()

    async def complete(self, volunteer_id: int, course_id: int, semester: int) -> None:
        await self.session.execute(
            insert(course_completions).values(
                volunteer_id=volunteer_id, course_id=course_id, completed_semester=semester
            )
        )

    async def assignments(self, shift: str) -> list[tuple[str, int, bool]]:
        rows = (
            await self.session.execute(
                select(shift_slots.c.kind, shift_assignments.c.volunteer_id, shift_assignments.c.cannot_attend)
                .select_from(shift_assignments.join(shift_slots, shift_slots.c.id == shift_assignments.c.slot_id))
                .where(shift_slots.c.shift_id == self.shift_ids[shift])
                .order_by(shift_assignments.c.id)
            )
        ).all()
        return [tuple(row) for row in rows]


@pytest.fixture
async def world(monkeypatch):
    # SQLite only autoincrements INTEGER primary keys; emulate production ids.
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
    async with async_sessionmaker(engine)() as session:
        token = set_request_session(session)
        try:
            yield await _build(session)
        finally:
            reset_request_session(token)
    await engine.dispose()


async def _build(session) -> World:
    clock = Clock()
    w = World(session, clock)
    now = clock.now
    for group_id, name in ((STJERNE, "Stjerne"), (HALVTIMEN, "Halvtimen"), (KULTUR, "Kultur")):
        await session.execute(
            insert(groups).values(
                id=group_id, slug=name.lower(), name=name, is_active=True, active_through_semester=20992
            )
        )
    people = {
        SIGRID: ("Sigrid", "Skiftleder", date(1999, 3, 1)),
        SANDER: ("Sander", "Skjenker", date(2001, 5, 5)),
        TORA: ("Tora", "Trainee", date(2004, 8, 8)),
        OLA: ("Ola", "Samf", date(2000, 1, 1)),
        UNGE: ("Unn", "Ung", date(2007, 6, 1)),
        NOBODY: ("Nils", "Ingen", date(1998, 2, 2)),
    }
    for volunteer_id, (first, last, birth) in people.items():
        await session.execute(
            insert(volunteer_records).values(
                id=volunteer_id, first_name=first, last_name=last, birth_date=birth, gender="A", created_at=now
            )
        )
    roles = {}
    for role_id, (group_id, name) in enumerate(
        [(STJERNE, "Skiftleder"), (STJERNE, "Skjenker"), (STJERNE, "Opplæring"), (KULTUR, "Medlem")], start=1
    ):
        await session.execute(insert(assignment_roles).values(id=role_id, group_id=group_id, name=name, penguin_points=1))
        roles[(group_id, name)] = role_id
    for volunteer_id, group_id, role in (
        (SIGRID, STJERNE, "Skiftleder"),
        (SANDER, STJERNE, "Skjenker"),
        (TORA, STJERNE, "Opplæring"),
        (OLA, KULTUR, "Medlem"),
        (UNGE, KULTUR, "Medlem"),
    ):
        await session.execute(
            insert(role_assignments).values(
                volunteer_id=volunteer_id,
                group_id=group_id,
                role_id=roles[(group_id, role)],
                semester=SEMESTER,
                contract_signed=True,
            )
        )
    # Templates make Stjerne and Halvtimen "groups with shifts".
    for template_id, (group_id, weekday) in enumerate(
        [(STJERNE, 1), (STJERNE, 2), (STJERNE, 3), (HALVTIMEN, 4)], start=1
    ):
        await session.execute(
            insert(shift_templates).values(
                id=template_id,
                group_id=group_id,
                venue="Halvtimen" if group_id == HALVTIMEN else "Stjernesalen",
                title="Skift",
                weekday=weekday,
                start_time=time(11, 30),
                end_time=time(17, 0),
                training_replaces_slot=True,
                is_active=True,
            )
        )
    for group_id in (STJERNE, HALVTIMEN):
        await session.execute(
            insert(shift_group_settings).values(
                group_id=group_id,
                app_enabled=True,
                samf_enabled=True,
                courses_enabled=True,
                meeting_point="Møt skiftleder 30 minutter før." if group_id == STJERNE else None,
                updated_at=now,
            )
        )

    day_slots = [("skiftleder", 1, False), ("skjenker", 1, True), ("opplaering", 1, False)]
    specs = {
        # name: group, venue, title, start, end, slots
        "tue": (STJERNE, "Stjernesalen", "Dagskift", oslo(date(2026, 11, 3), 11, 30), oslo(date(2026, 11, 3), 17), day_slots),
        "thu": (
            STJERNE,
            "Stjernesalen",
            "Dagskift",
            oslo(date(2026, 11, 5), 11, 30),
            oslo(date(2026, 11, 5), 17),
            [("skiftleder", 1, False), ("skjenker", 1, False), ("opplaering", 1, False)],
        ),
        "fri": (
            HALVTIMEN,
            "Halvtimen",
            "Åpningsskift",
            oslo(date(2026, 11, 6), 20, 30),
            oslo(date(2026, 11, 7), 3, 30),
            [("skiftleder", 1, False), ("bartender", 2, True)],
        ),
        "dec": (STJERNE, "Stjernesalen", "Dagskift", oslo(date(2026, 12, 2), 11, 30), oslo(date(2026, 12, 2), 17), day_slots),
    }
    for name, (group_id, venue, title, starts_at, ends_at, slots) in specs.items():
        shift_id = (
            await session.execute(
                insert(shifts)
                .values(
                    group_id=group_id,
                    venue=venue,
                    title=title,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    is_closed=False,
                    training_replaces_slot=True,
                )
                .returning(shifts.c.id)
            )
        ).scalar_one()
        w.shift_ids[name] = shift_id
        for order, (kind, count, open_for_samf) in enumerate(slots):
            slot_id = (
                await session.execute(
                    insert(shift_slots)
                    .values(shift_id=shift_id, kind=kind, count=count, open_for_samf=open_for_samf, sort_order=order)
                    .returning(shift_slots.c.id)
                )
            ).scalar_one()
            w.slots[(name, kind)] = slot_id

    await session.execute(insert(courses).values(id=BRANN, name="Brannkurs"))
    await session.execute(insert(courses).values(id=BAR, name="Barkurs"))
    for name, course_id, starts_at, capacity in (
        ("brann-2nov", BRANN, oslo(date(2026, 11, 2), 18), 1),
        ("brann-20nov", BRANN, oslo(date(2026, 11, 20), 18), 10),
        ("bar-4nov", BAR, oslo(date(2026, 11, 4), 17), 5),
    ):
        w.sessions[name] = (
            await session.execute(
                insert(course_sessions)
                .values(course_id=course_id, starts_at=starts_at, location="Kvarteret", capacity=capacity, is_cancelled=False)
                .returning(course_sessions.c.id)
            )
        ).scalar_one()
    # Eligibility comes from qualification rows, not verv.
    await session.execute(
        insert(volunteer_qualifications).values(
            [
                {"volunteer_id": SIGRID, "qualification": "skiftleder", "status": "approved", "group_id": STJERNE, "takes_trainees": True},
                {"volunteer_id": SIGRID, "qualification": "skjenker", "status": "approved", "group_id": STJERNE, "takes_trainees": False},
                {"volunteer_id": SANDER, "qualification": "skjenker", "status": "approved", "group_id": STJERNE, "takes_trainees": False},
                {"volunteer_id": TORA, "qualification": "skjenker", "status": "training", "group_id": STJERNE, "takes_trainees": False, "training_target": 3},
            ]
        )
    )
    await w.complete(SIGRID, BRANN, 20261)  # valid through autumn 2027
    await w.complete(SANDER, BRANN, 20242)  # ran out after spring 2026
    await w.complete(TORA, BRANN, 20262)
    return w


def place(response, shift_id: int, kind: str):
    shift = next((s for s in response.shifts if s.shift_id == shift_id), None)
    if shift is None:
        return None
    return next((p for p in shift.places if p.kind == kind), None)


# ---------------------------------------------------------------- config


async def test_config_for_a_skjenke_member(world: World) -> None:
    config = await world.service.get_config(SANDER)
    assert config.enabled and config.audience == "skjenke"
    assert [(t.id, t.label, t.scope) for t in config.tabs] == [
        ("wishes", "Ønsker", None),
        ("open", "Stjerne", "own"),
        ("open", "Andre", "other"),
        ("mine", "Mine", None),
    ]
    stjerne = next(g for g in config.groups if g.group_id == STJERNE)
    assert stjerne.is_member and stjerne.role == "Skjenker"
    assert stjerne.quota_text == "Kontrakt: 1 skift i uka"
    assert stjerne.meeting_point == "Møt skiftleder 30 minutter før."
    assert {k.kind for k in config.slot_kinds} >= {"skiftleder", "skjenker", "opplaering"}
    assert [a.value for a in config.wish_answers] == ["vil", "kan", "nei"]


async def test_config_for_samfunnettjeneste_and_for_no_access(world: World) -> None:
    samf = await world.service.get_config(OLA)
    assert samf.audience == "samfunnettjeneste"
    assert [(t.id, t.label) for t in samf.tabs] == [("open", "Ledige skift"), ("mine", "Mine skift")]

    for volunteer_id in (NOBODY, None, 999):
        config = await world.service.get_config(volunteer_id)
        assert not config.enabled and config.audience == "none" and config.tabs == []


async def test_switching_a_group_off_hides_it(world: World) -> None:
    await world.session.execute(update(shift_group_settings).values(app_enabled=False))
    assert (await world.service.get_config(SANDER)).audience == "none"
    with pytest.raises(ShiftAccessError):
        await world.service.list_open(OLA, scope="all", days=14)


async def test_trainee_sees_training_progress(world: World) -> None:
    config = await world.service.get_config(TORA)
    stjerne = next(g for g in config.groups if g.group_id == STJERNE)
    assert stjerne.in_training and stjerne.training_done == 0 and stjerne.training_needed == 3


# ---------------------------------------------------------------- open places


async def test_samf_sees_only_open_places_with_course_status(world: World) -> None:
    listing = await world.service.list_open(OLA, scope="all", days=14)
    tue = world.shift_ids["tue"]
    assert place(listing, tue, "skiftleder") is None
    assert place(listing, tue, "opplaering") is None
    skjenker = place(listing, tue, "skjenker")
    # Barkurs on 4 Nov is after the shift on 3 Nov.
    assert skjenker.tone == "off" and skjenker.status_text == "Ingen barkurs før skiftet"
    assert skjenker.counts_as_samfunnettjeneste and not skjenker.can_claim
    # Thursday's skjenker place is only for Skjenke.
    assert place(listing, world.shift_ids["thu"], "skjenker") is None
    bartender = place(listing, world.shift_ids["fri"], "bartender")
    assert bartender.tone == "warn"
    assert bartender.status_text == "Meld deg på brannkurs først · kurs 2. nov"
    assert [o.course_key for o in bartender.needs_course_signup] == ["brann", "bar"]
    assert bartender.open_places == 2


async def add_stjerne_shift(world: World, starts_at: datetime, ends_at: datetime) -> tuple[int, int]:
    shift_id = (
        await world.session.execute(
            insert(shifts)
            .values(
                group_id=STJERNE,
                venue="Stjernesalen",
                title="Skift",
                starts_at=starts_at,
                ends_at=ends_at,
                is_closed=False,
                training_replaces_slot=True,
            )
            .returning(shifts.c.id)
        )
    ).scalar_one()
    slot_id = (
        await world.session.execute(
            insert(shift_slots)
            .values(shift_id=shift_id, kind="skjenker", count=1, open_for_samf=False, sort_order=0)
            .returning(shift_slots.c.id)
        )
    ).scalar_one()
    return shift_id, slot_id


async def test_unallocated_weeks_are_open_by_default(world: World) -> None:
    # Clock is Monday 2 Nov. This week (tue) has no row and counts as published;
    # next week and later (dec) are open.
    next_week, next_slot = await add_stjerne_shift(world, oslo(date(2026, 11, 10), 11), oslo(date(2026, 11, 10), 17))
    listing = await world.service.list_open(SIGRID, scope="own", days=40)
    ids = {s.shift_id for s in listing.shifts}
    assert world.shift_ids["tue"] in ids
    assert next_week not in ids and world.shift_ids["dec"] not in ids

    with pytest.raises(ShiftNotFoundError):
        await world.service.claim(SANDER, next_slot, ClaimPlaceRequest())

    wishes = await world.service.set_shift_wish(SANDER, next_week, "vil")
    assert next_week in {s.shift_id for s in wishes.shifts}
    with pytest.raises(ShiftPlacementError, match="publisert"):
        await world.service.set_shift_wish(SANDER, world.shift_ids["tue"], "vil")


async def test_shift_past_midnight_sunday_belongs_to_the_sunday_week(world: World) -> None:
    # Sunday 8 Nov 23:00 to Monday 9 Nov 02:00: this week, which is published,
    # although it ends in the open week.
    shift_id, _ = await add_stjerne_shift(world, oslo(date(2026, 11, 8), 23), oslo(date(2026, 11, 9), 2))
    listing = await world.service.list_open(SIGRID, scope="own", days=40)
    assert shift_id in {s.shift_id for s in listing.shifts}
    with pytest.raises(ShiftPlacementError, match="publisert"):
        await world.service.set_shift_wish(SANDER, shift_id, "vil")


async def test_explicit_published_week_overrides_the_open_default(world: World) -> None:
    await world.session.execute(
        insert(shift_week_status).values(group_id=STJERNE, week_start=date(2026, 11, 30), status="published")
    )
    listing = await world.service.list_open(SIGRID, scope="own", days=40)
    assert world.shift_ids["dec"] in {s.shift_id for s in listing.shifts}
    with pytest.raises(ShiftPlacementError, match="publisert"):
        await world.service.set_shift_wish(SANDER, world.shift_ids["dec"], "vil")


async def test_explicit_open_week_hides_a_current_week(world: World) -> None:
    await world.session.execute(
        insert(shift_week_status).values(group_id=STJERNE, week_start=date(2026, 11, 2), status="allocating")
    )
    listing = await world.service.list_open(SIGRID, scope="own", days=40)
    assert world.shift_ids["tue"] not in {s.shift_id for s in listing.shifts}


async def test_wishes_list_weeks_with_shifts_up_to_the_end_of_the_semester(world: World) -> None:
    world.clock.now = oslo(date(2026, 8, 10), 9)
    await add_stjerne_shift(world, oslo(date(2026, 12, 30), 11), oslo(date(2026, 12, 30), 17))
    await add_stjerne_shift(world, oslo(date(2027, 1, 5), 11), oslo(date(2027, 1, 5), 17))
    wishes = await world.service.get_wishes(SANDER)
    # Only weeks with shifts, and nothing after the semester (January is next semester).
    assert [w.week_start for w in wishes.weeks] == [date(2026, 11, 2), date(2026, 11, 30), date(2026, 12, 28)]
    assert {w.status for w in wishes.weeks} == {"open"}
    assert all(w.group_id == STJERNE and w.group_name == "Stjerne" for w in wishes.weeks)


async def test_allocating_week_reads_as_open_for_volunteers(world: World) -> None:
    await world.session.execute(
        insert(shift_week_status).values(group_id=STJERNE, week_start=date(2026, 11, 30), status="allocating")
    )
    wishes = await world.service.get_wishes(SANDER)
    december = next(w for w in wishes.weeks if w.week_start == date(2026, 11, 30))
    assert december.accepts_wishes
    assert december.status_label == "Åpen for ønsker"
    assert "Fordeles" not in december.status_label + december.note
    assert december.note == PERIOD_STATUSES["open"][1]


async def test_wishes_in_december_reach_into_january(world: World) -> None:
    world.clock.now = oslo(date(2026, 12, 3), 9)
    jan, _ = await add_stjerne_shift(world, oslo(date(2027, 1, 26), 11), oslo(date(2027, 1, 26), 17))
    feb, _ = await add_stjerne_shift(world, oslo(date(2027, 2, 2), 11), oslo(date(2027, 2, 2), 17))
    wishes = await world.service.get_wishes(SANDER)
    ids = {s.shift_id for s in wishes.shifts}
    assert jan in ids and feb not in ids
    assert wishes.weeks[-1].week_start == date(2027, 1, 25)


async def test_open_list_says_when_more_places_lie_beyond_the_window(world: World) -> None:
    # Clock is Mon 2 Nov. Week of 23 Nov is published explicitly, so it counts.
    await world.session.execute(
        insert(shift_week_status).values(group_id=STJERNE, week_start=date(2026, 11, 23), status="published")
    )
    later, _ = await add_stjerne_shift(world, oslo(date(2026, 11, 25), 11), oslo(date(2026, 11, 25), 17))

    short = await world.service.list_open(SIGRID, scope="own", days=14)
    assert later not in {s.shift_id for s in short.shifts}
    assert short.has_more is True

    long = await world.service.list_open(SIGRID, scope="own", days=30)
    assert later in {s.shift_id for s in long.shifts}
    assert long.has_more is False


async def test_open_list_has_no_more_when_nothing_is_beyond_the_window(world: World) -> None:
    # December is open (unpublished), so it is not a place anyone can take.
    short = await world.service.list_open(SIGRID, scope="own", days=14)
    assert short.has_more is False
    # A published shift past the 92-day maximum does not count either.
    await world.session.execute(
        insert(shift_week_status).values(group_id=STJERNE, week_start=date(2027, 3, 1), status="published")
    )
    await add_stjerne_shift(world, oslo(date(2027, 3, 3), 11), oslo(date(2027, 3, 3), 17))
    assert (await world.service.list_open(SIGRID, scope="own", days=14)).has_more is False


async def test_my_shifts_warn_about_a_second_shift_in_the_week(world: World) -> None:
    await world.place("tue", "skjenker", SANDER)
    mine = await world.service.list_mine(SANDER)
    assert [s.warnings for s in mine.shifts] == [[]]

    await world.place("thu", "skjenker", SANDER)
    mine = await world.service.list_mine(SANDER)
    expected = ["Du har 2 skift i uke 45. Kontrakten din er 1 skift i uka."]
    assert [s.warnings for s in mine.shifts] == [expected, expected]


async def test_trainee_gets_the_training_place_not_the_crew_place(world: World) -> None:
    listing = await world.service.list_open(TORA, scope="own", days=14)
    tue = world.shift_ids["tue"]
    assert place(listing, tue, "skjenker") is None
    training = place(listing, tue, "opplaering")
    assert training.tone == "info" and training.status_text == "Opplæringsskift 1 av 3"
    detail = await world.service.get_place(TORA, world.slots[("tue", "opplaering")])
    assert any(r.text == "Ingen utlært på skiftet ennå." for r in detail.requirements)
    assert detail.claim_label == "Ta skiftet"


async def test_halvtimen_requires_20(world: World) -> None:
    await world.complete(UNGE, BRANN, SEMESTER)
    await world.complete(UNGE, BAR, SEMESTER)
    listing = await world.service.list_open(UNGE, scope="all", days=14)
    bartender = place(listing, world.shift_ids["fri"], "bartender")
    assert bartender.tone == "off" and bartender.status_text == "Halvtimen krever 20 år"
    with pytest.raises(ShiftPlacementError, match="Halvtimen krever 20 år"):
        await world.service.claim(UNGE, world.slots[("fri", "bartender")], ClaimPlaceRequest())


# ---------------------------------------------------------------- claim


async def test_sign_up_for_courses_and_claim_in_one_step(world: World) -> None:
    slot_id = world.slots[("fri", "bartender")]
    detail = await world.service.get_place(OLA, slot_id)
    assert detail.claim_label == "Meld meg på brannkurs 2. nov og barkurs 4. nov, og ta skiftet"
    with pytest.raises(ShiftPlacementError, match="påmeldt brannkurs før skiftet"):
        await world.service.claim(OLA, slot_id, ClaimPlaceRequest())

    sessions = [offer.session_id for offer in detail.place.needs_course_signup]
    result = await world.service.claim(OLA, slot_id, ClaimPlaceRequest(course_session_ids=sessions))
    assert result.message == "Du er påmeldt brannkurs og barkurs og satt opp på skiftet."
    assert result.shift.tag == "Teller som samfunnettjeneste"
    assert result.shift.meeting_point is None
    assert ("bartender", OLA, False) in await world.assignments("fri")
    signed = (
        await world.session.execute(
            select(course_session_signups.c.session_id).where(course_session_signups.c.volunteer_id == OLA)
        )
    ).scalars().all()
    assert sorted(signed) == sorted(sessions)

    mine = await world.service.list_mine(OLA)
    assert [s.shift_id for s in mine.shifts] == [world.shift_ids["fri"]]
    assert mine.quota is None
    assert mine.samfunnettjeneste.planned == 1 and mine.samfunnettjeneste.done == 0


async def test_course_checks_are_skipped_for_a_group_with_the_switch_off(world: World) -> None:
    slot = world.slots[("tue", "skjenker")]
    # On: Sander's brannkurs has run out, so the claim needs a course sign-up.
    detail = await world.service.get_place(SANDER, slot)
    assert detail.claim_label == "Meld meg på brannkurs 2. nov, og ta skiftet"
    assert detail.requirements

    await world.session.execute(
        update(shift_group_settings).where(shift_group_settings.c.group_id == STJERNE).values(courses_enabled=False)
    )
    detail = await world.service.get_place(SANDER, slot)
    assert detail.claim_label == "Ta skiftet"
    assert detail.requirements == []
    result = await world.service.claim(SANDER, slot, ClaimPlaceRequest(course_session_ids=[]))
    assert result.shift is not None
    assert (await world.assignments("tue")) == [("skjenker", SANDER, False)]


async def test_full_course_session_is_not_offered(world: World) -> None:
    # Sander's brannkurs ran out; the only course before Tuesday has one spot.
    detail = await world.service.get_place(SANDER, world.slots[("tue", "skjenker")])
    assert detail.claim_label == "Meld meg på brannkurs 2. nov, og ta skiftet"
    assert detail.place.status_text == "Forny brannkurs først · kurs 2. nov"
    await world.service.claim(
        SANDER,
        world.slots[("tue", "skjenker")],
        ClaimPlaceRequest(course_session_ids=[world.sessions["brann-2nov"]]),
    )
    # The 2 Nov course is now full, and it was Ola's only brannkurs before Friday.
    listing = await world.service.list_open(OLA, scope="all", days=14)
    bartender = place(listing, world.shift_ids["fri"], "bartender")
    assert bartender.tone == "off" and bartender.status_text == "Ingen brannkurs før skiftet"


async def test_second_shift_in_a_week_warns_but_is_allowed(world: World) -> None:
    await world.complete(SANDER, BRANN, SEMESTER)
    await world.service.claim(SANDER, world.slots[("tue", "skjenker")], ClaimPlaceRequest())
    listing = await world.service.list_open(SANDER, scope="own", days=14)
    assert world.shift_ids["tue"] not in {s.shift_id for s in listing.shifts}
    thursday = place(listing, world.shift_ids["thu"], "skjenker")
    assert thursday.tone == "warn" and thursday.status_text == "Har allerede et skift uke 45"
    assert thursday.can_claim
    await world.service.claim(SANDER, world.slots[("thu", "skjenker")], ClaimPlaceRequest())
    mine = await world.service.list_mine(SANDER)
    assert mine.quota.label == "Denne uka (uke 45)" and mine.quota.taken == 2 and mine.quota.limit == 1


async def test_taken_place_is_refused(world: World) -> None:
    await world.complete(SANDER, BRANN, SEMESTER)
    await world.place("tue", "skjenker", SIGRID)
    with pytest.raises(ShiftPlacementError, match="Plassen ble nettopp tatt"):
        await world.service.claim(SANDER, world.slots[("tue", "skjenker")], ClaimPlaceRequest())
    with pytest.raises(ShiftNotFoundError):
        await world.service.claim(SANDER, 9999, ClaimPlaceRequest())


# ---------------------------------------------------------------- swap and "kan ikke"


async def test_swap_request_and_cancel(world: World) -> None:
    assignment = await world.place("tue", "skjenker", SANDER)
    result = await world.service.request_swap(SANDER, assignment, "  Eksamen  ")
    assert result.shift.swap_requested and result.shift.can_request_swap
    # No notification exists yet, so the text must not promise that leaders are told.
    assert result.message.startswith("Markert som ønsker bytte. Lederne får ikke varsel ennå")
    assert "si også fra til skiftlederen" in result.message
    assert "Lederne ser" not in result.message
    message = (
        await world.session.execute(
            select(shift_assignments.c.status_message).where(shift_assignments.c.id == assignment)
        )
    ).scalar_one()
    assert message == "Eksamen"
    result = await world.service.cancel_swap(SANDER, assignment)
    assert not result.shift.swap_requested
    with pytest.raises(ShiftNotFoundError):
        await world.service.request_swap(OLA, assignment, None)


async def test_cannot_attend_opens_the_place_and_the_taker_replaces(world: World) -> None:
    assignment = await world.place("thu", "skjenker", SANDER)
    with pytest.raises(ShiftPlacementError, match="for selve dagen"):
        await world.service.report_cannot_attend(SANDER, assignment, None)

    world.clock.now = oslo(date(2026, 11, 5), 7)
    result = await world.service.report_cannot_attend(SANDER, assignment, None)
    assert result.shift.cannot_attend and not result.shift.can_report_cannot_attend
    assert result.message.startswith("Markert. Lederne får ikke varsel ennå, så si også fra til skiftlederen.")
    assert "Lederne ser" not in result.message

    # Thursday's skjenker place was only for Skjenke; now it is open to everyone.
    await world.complete(OLA, BRANN, SEMESTER)
    await world.complete(OLA, BAR, SEMESTER)
    listing = await world.service.list_open(OLA, scope="all", days=14)
    opened = place(listing, world.shift_ids["thu"], "skjenker")
    assert opened.opened_by_absence and opened.can_claim
    detail = await world.service.get_place(OLA, world.slots[("thu", "skjenker")])
    assert all(member.first_name != "Sander" for member in detail.crew)

    await world.service.claim(OLA, world.slots[("thu", "skjenker")], ClaimPlaceRequest())
    assert await world.assignments("thu") == [("skjenker", OLA, False)]
    with pytest.raises(ShiftNotFoundError):
        await world.service.undo_cannot_attend(SANDER, assignment)


async def test_undo_cannot_attend(world: World) -> None:
    assignment = await world.place("tue", "skjenker", SANDER)
    world.clock.now = oslo(date(2026, 11, 3), 8)
    await world.service.report_cannot_attend(SANDER, assignment, "Syk")
    result = await world.service.undo_cannot_attend(SANDER, assignment)
    assert not result.shift.cannot_attend and result.shift.can_report_cannot_attend
    # The shift starts 11:30, so a swap request is still possible at 08:00.
    result = await world.service.request_swap(SANDER, assignment, None)
    assert result.shift.swap_requested


async def test_swap_is_not_possible_after_start(world: World) -> None:
    assignment = await world.place("tue", "skjenker", SANDER)
    world.clock.now = oslo(date(2026, 11, 3), 12)
    with pytest.raises(ShiftPlacementError, match="Skiftet har startet"):
        await world.service.request_swap(SANDER, assignment, None)
    world.clock.now = oslo(date(2026, 11, 3), 18)
    with pytest.raises(ShiftNotFoundError, match="over"):
        await world.service.report_cannot_attend(SANDER, assignment, None)


# ---------------------------------------------------------------- wishes


async def test_weekday_rules_and_shift_wishes(world: World) -> None:
    wishes = await world.service.get_wishes(SANDER)
    assert [r.weekday for r in wishes.rules] == [1, 2, 3]
    assert [x.shift_id for x in wishes.shifts] == [world.shift_ids["dec"]]
    assert {w.week_start: w.status for w in wishes.weeks} == {date(2026, 11, 2): "published", date(2026, 11, 30): "open"}

    wishes = await world.service.set_weekday_rule(SANDER, 2, "kan")
    assert next(r for r in wishes.rules if r.weekday == 2).answer == "kan"

    wishes = await world.service.get_wishes(SANDER)
    december = next(w for w in wishes.weeks if w.week_start == date(2026, 11, 30))
    assert december.week_number == 49 and december.label == "Uke 49 · 30. nov–6. des"
    assert december.accepts_wishes and december.status_label == "Åpen for ønsker"
    [shift] = wishes.shifts
    assert shift.shift_id == world.shift_ids["dec"] and shift.answer == "kan" and shift.from_rule

    wishes = await world.service.set_shift_wish(SANDER, world.shift_ids["dec"], "nei")
    assert wishes.shifts[0].answer == "nei" and not wishes.shifts[0].from_rule
    wishes = await world.service.set_shift_wish(SANDER, world.shift_ids["dec"], None)
    assert wishes.shifts[0].answer == "kan" and wishes.shifts[0].from_rule

    with pytest.raises(ShiftPlacementError, match="publisert"):
        await world.service.set_shift_wish(SANDER, world.shift_ids["tue"], "vil")
    with pytest.raises(ShiftNotFoundError):
        await world.service.set_shift_wish(SANDER, world.shift_ids["fri"], "vil")


async def test_wishes_are_for_skjenke_members(world: World) -> None:
    with pytest.raises(ShiftAccessError):
        await world.service.get_wishes(OLA)


# ---------------------------------------------------------------- courses


async def test_course_status_and_sign_up(world: World) -> None:
    courses_now = await world.service.get_courses(OLA)
    assert [c.key for c in courses_now.courses] == ["brann", "bar"]
    brann = courses_now.courses[0]
    assert not brann.valid and brann.status_text.startswith("Ikke tatt. Neste kurs mandag 2. november")

    sander = await world.service.get_courses(SANDER)
    assert [c.key for c in sander.courses] == ["brann"]
    assert sander.courses[0].status_text.startswith("Gikk ut etter våren 2026.")
    sigrid = await world.service.get_courses(SIGRID)
    assert sigrid.courses[0].valid and sigrid.courses[0].status_text == "Gyldig ut høsten 2027."

    result = await world.service.sign_up(OLA, world.sessions["brann-20nov"])
    assert result.courses[0].signed_up.session_id == world.sessions["brann-20nov"]
    result = await world.service.leave_session(OLA, world.sessions["brann-20nov"])
    assert result.courses[0].signed_up is None


async def test_full_session_and_sign_up_a_shift_depends_on(world: World) -> None:
    await world.service.sign_up(SANDER, world.sessions["brann-2nov"])
    with pytest.raises(ShiftPlacementError, match="fullt"):
        await world.service.sign_up(OLA, world.sessions["brann-2nov"])

    detail = await world.service.get_place(OLA, world.slots[("fri", "bartender")])
    assert detail.claim_label is None  # no brannkurs with room before Friday

    await world.service.claim(SANDER, world.slots[("tue", "skjenker")], ClaimPlaceRequest())
    with pytest.raises(ShiftPlacementError, match="før skiftet tirsdag 3. november"):
        await world.service.leave_session(SANDER, world.sessions["brann-2nov"])
    with pytest.raises(ShiftPlacementError, match="Velg en dato før det"):
        await world.service.sign_up(SANDER, world.sessions["brann-20nov"])


async def test_deleting_a_volunteer_removes_their_shift_rows(world: World) -> None:
    session = world.session
    await world.place("tue", "skjenker", SANDER)
    await world.place("tue", "skiftleder", SIGRID)
    await world.service.set_weekday_rule(SANDER, 2, "vil")
    await world.service.set_shift_wish(SANDER, world.shift_ids["dec"], "kan")
    await session.execute(insert(course_session_signups).values(session_id=world.sessions["brann-20nov"], volunteer_id=SANDER))

    await session.execute(delete(volunteer_qualifications))
    await session.execute(
        insert(volunteer_qualifications).values(
            [
                {"volunteer_id": SANDER, "qualification": "skjenker", "status": "approved", "group_id": STJERNE, "takes_trainees": False},
                {"volunteer_id": SANDER, "qualification": "kokk", "status": "training", "group_id": STJERNE, "takes_trainees": False},
                {"volunteer_id": SIGRID, "qualification": "skiftleder", "status": "approved", "group_id": STJERNE, "takes_trainees": True},
            ]
        )
    )

    await VolunteersRepository().delete_volunteer(SANDER)

    for table in (
        shift_assignments,
        shift_wishes,
        shift_availability_rules,
        course_session_signups,
        volunteer_qualifications,
    ):
        rows = (await session.execute(select(table.c.volunteer_id))).scalars().all()
        assert SANDER not in rows
    # Other people and the shifts themselves are untouched.
    assert await world.assignments("tue") == [("skiftleder", SIGRID, False)]
    assert (await session.execute(select(volunteer_qualifications.c.volunteer_id))).scalars().all() == [SIGRID]
    assert (await session.execute(select(shifts.c.id).where(shifts.c.id == world.shift_ids["tue"]))).scalar_one()
    assert SANDER not in (await session.execute(select(volunteer_records.c.id))).scalars().all()


@pytest.mark.asyncio
async def test_leader_wish_queries_read_the_requested_people_and_shifts_only(world: World) -> None:
    from app.domain.shifts.repository import ShiftsRepository

    shift, _ = await add_stjerne_shift(world, oslo(date(2026, 11, 10), 11), oslo(date(2026, 11, 10), 17))
    await world.service.set_shift_wish(SANDER, shift, "vil")
    await world.service.set_weekday_rule(SANDER, 1, "nei")
    await world.service.set_weekday_rule(TORA, 1, "kan")
    repository = ShiftsRepository()

    assert await repository.list_wishes_for([shift], [SANDER, TORA]) == {(shift, SANDER): "vil"}
    assert await repository.list_wishes_for([shift], [TORA]) == {}
    assert await repository.list_rules_for([SEMESTER], [SANDER, TORA]) == {
        (SANDER, SEMESTER, 1): "nei",
        (TORA, SEMESTER, 1): "kan",
    }
    assert await repository.list_rules_for([SEMESTER], [TORA]) == {(TORA, SEMESTER, 1): "kan"}
    assert await repository.list_rules_for([SEMESTER - 1], [SANDER]) == {}
    assert await repository.list_wishes_for([], [SANDER]) == {}


@pytest.mark.asyncio
async def test_qualification_reads_return_rows_per_person_and_per_group(world: World) -> None:
    from app.domain.shifts.models import QualificationRecord
    from app.domain.shifts.repository import ShiftsRepository

    await world.session.execute(delete(volunteer_qualifications))
    await world.session.execute(
        insert(volunteer_qualifications).values(
            [
                {"volunteer_id": SANDER, "qualification": "skjenker", "status": "approved", "group_id": STJERNE, "takes_trainees": False, "training_target": None},
                {"volunteer_id": SIGRID, "qualification": "skiftleder", "status": "approved", "group_id": STJERNE, "takes_trainees": True, "training_target": None},
                {"volunteer_id": TORA, "qualification": "skjenker", "status": "training", "group_id": HALVTIMEN, "takes_trainees": False, "training_target": 3},
            ]
        )
    )
    repository = ShiftsRepository()

    assert await repository.list_qualifications([]) == []
    sander = await repository.list_qualifications([SANDER])
    assert sander == [
        QualificationRecord(
            volunteer_id=SANDER,
            qualification="skjenker",
            status="approved",
            group_id=STJERNE,
            training_target=None,
            takes_trainees=False,
            approved_by=None,
            approved_at=None,
        )
    ]
    both = await repository.list_qualifications([SIGRID, TORA])
    assert [(row.volunteer_id, row.qualification, row.status) for row in both] == [
        (SIGRID, "skiftleder", "approved"),
        (TORA, "skjenker", "training"),
    ]
    assert both[0].takes_trainees is True
    assert both[1].training_target == 3

    in_halvtimen = await repository.list_group_qualifications([HALVTIMEN])
    assert [row.volunteer_id for row in in_halvtimen] == [TORA]
    assert await repository.list_group_qualifications([]) == []
    assert await repository.list_group_qualifications([KULTUR]) == []


@pytest.mark.asyncio
async def test_qualification_settings_default_when_a_group_has_no_row(world: World) -> None:
    from app.domain.shifts.models import QualificationSettings
    from app.domain.shifts.repository import ShiftsRepository

    await world.session.execute(
        update(shift_group_settings)
        .where(shift_group_settings.c.group_id == HALVTIMEN)
        .values(training_shifts_needed=5, sibling_skiftleder_allowed=True)
    )
    repository = ShiftsRepository()

    assert await repository.get_qualification_settings([]) == {}
    assert await repository.get_qualification_settings([STJERNE, HALVTIMEN, KULTUR]) == {
        STJERNE: QualificationSettings(training_shifts_needed=3, sibling_skiftleder_allowed=False),
        HALVTIMEN: QualificationSettings(training_shifts_needed=5, sibling_skiftleder_allowed=True),
        KULTUR: QualificationSettings(),
    }


# ---------------------------------------------------------------- eligibility from qualifications


async def test_claim_needs_the_qualification_not_the_verv(world: World) -> None:
    slot_id = world.slots[("thu", "skjenker")]  # not open for samfunnettjeneste
    # Sigrid has verv Skiftleder; the skjenker row is what counts.
    await world.session.execute(
        delete(volunteer_qualifications).where(
            volunteer_qualifications.c.volunteer_id == SIGRID,
            volunteer_qualifications.c.qualification == "skjenker",
        )
    )
    listing = await world.service.list_open(SIGRID, scope="own", days=40)
    assert place(listing, world.shift_ids["thu"], "skjenker") is None
    with pytest.raises(ShiftPlacementError, match="Kun for godkjente skjenkere"):
        await world.service.claim(SIGRID, slot_id, ClaimPlaceRequest())

    await world.session.execute(
        insert(volunteer_qualifications).values(
            volunteer_id=SIGRID, qualification="skjenker", status="approved", group_id=STJERNE, takes_trainees=False
        )
    )
    await world.service.claim(SIGRID, slot_id, ClaimPlaceRequest())
    assert ("skjenker", SIGRID, False) in await world.assignments("thu")


async def test_trainee_is_refused_crew_places_and_gets_the_training_place(world: World) -> None:
    training = await world.service.get_place(TORA, world.slots[("tue", "opplaering")])
    assert training.place.can_claim
    with pytest.raises(ShiftPlacementError, match="bruk opplæringsplassen"):
        await world.service.claim(TORA, world.slots[("thu", "skjenker")], ClaimPlaceRequest())


async def test_opplaering_needs_a_training_row_and_is_not_opened_by_kan_ikke(world: World) -> None:
    slot_id = world.slots[("tue", "opplaering")]
    # Sander is a member with an approved row, not in training: no opplaering place.
    with pytest.raises(ShiftPlacementError, match="i opplæring i gruppen"):
        await world.service.claim(SANDER, slot_id, ClaimPlaceRequest())
    with pytest.raises(ShiftPlacementError, match="i opplæring i gruppen"):
        await world.service.claim(OLA, slot_id, ClaimPlaceRequest())  # samfunnettjeneste
    listing = await world.service.list_open(SANDER, scope="own", days=40)
    assert place(listing, world.shift_ids["tue"], "opplaering") is None

    # A "kan ikke" on the opplaering place does not open it to anyone else.
    await world.place("tue", "opplaering", TORA)
    await world.session.execute(update(shift_assignments).values(cannot_attend=True))
    listing = await world.service.list_open(OLA, scope="all", days=40)
    assert place(listing, world.shift_ids["tue"], "opplaering") is None
    listing = await world.service.list_open(SANDER, scope="own", days=40)
    assert place(listing, world.shift_ids["tue"], "opplaering") is None


async def test_kan_ikke_opens_a_skjenker_place_but_not_a_skiftleder_place(world: World) -> None:
    await world.place("thu", "skjenker", SIGRID)
    await world.place("thu", "skiftleder", SANDER)
    await world.session.execute(update(shift_assignments).values(cannot_attend=True))
    # Ola is samfunnettjeneste with no rows: the sick person's skjenker place opens to everyone.
    listing = await world.service.list_open(OLA, scope="all", days=40)
    assert place(listing, world.shift_ids["thu"], "skjenker") is not None
    assert place(listing, world.shift_ids["thu"], "skiftleder") is None


async def test_app_evaluation_and_board_check_give_the_same_answer(world: World) -> None:
    from app.domain.shifts.planning import open_for_everyone

    # Give Sander a kokk training row too, so the five people have different row sets
    # (their verv are not read).
    await world.session.execute(
        insert(volunteer_qualifications).values(
            volunteer_id=SANDER, qualification="kokk", status="training", group_id=STJERNE, takes_trainees=False
        )
    )
    checked = 0
    for volunteer_id in (SIGRID, SANDER, TORA, OLA, NOBODY):
        person = await world.service._person_or_none(volunteer_id)
        for shift_name in ("tue", "thu", "fri"):
            record = await world.service.repository.get_shift(world.shift_ids[shift_name])
            for slot in record.slots:
                expected = person.eligibility.problem(slot.kind, record.group_id, open_for_everyone(slot))
                try:
                    await world.service.shifts._check(record, slot, volunteer_id)
                except ShiftPlacementError as error:
                    message = str(error)
                else:
                    message = None
                if expected is None:
                    assert message is None or not message.startswith(("Kun for", "I opplæring")), message
                else:
                    assert message == expected + ".", (volunteer_id, shift_name, slot.kind)
                checked += 1
    assert checked > 30


async def test_the_app_reads_qualifications_once_per_request_not_per_slot(world: World) -> None:
    repository = world.service.repository
    reads = []
    original = repository.list_qualifications

    async def counting(volunteer_ids):
        reads.append(list(volunteer_ids))
        return await original(volunteer_ids)

    repository.list_qualifications = counting  # type: ignore[method-assign]
    listing = await world.service.list_open(SANDER, scope="own", days=40)
    assert listing.shifts  # several shifts and places were evaluated
    assert reads == [[SANDER]]

    reads.clear()
    await world.service.claim(SIGRID, world.slots[("thu", "skjenker")], ClaimPlaceRequest())
    assert reads == [[SIGRID]]  # the board check reuses the built context


# ---------------------------------------------------------------- parent-only members, visitors, clock

PARENT, PARENT_ONLY = 4, 16


async def _add_parent_group(world: World) -> None:
    """Stjerne and Halvtimen below a new parent group, as siblings."""
    session = world.session
    await session.execute(
        insert(groups).values(id=PARENT, slug="skjenke", name="Skjenke", is_active=True, active_through_semester=20992)
    )
    await session.execute(update(groups).where(groups.c.id.in_((STJERNE, HALVTIMEN))).values(parent_group_id=PARENT))


async def test_a_member_of_the_parent_group_only_gets_every_bar_below(world: World) -> None:
    await _add_parent_group(world)
    session = world.session
    await session.execute(
        insert(volunteer_records).values(
            id=PARENT_ONLY, first_name="Pia", last_name="Parent", birth_date=date(1998, 4, 4), gender="A",
            created_at=world.clock.now,
        )
    )
    await session.execute(insert(assignment_roles).values(id=9, group_id=PARENT, name="Medlem", penguin_points=1))
    await session.execute(
        insert(role_assignments).values(
            volunteer_id=PARENT_ONLY, group_id=PARENT, role_id=9, semester=SEMESTER, contract_signed=True
        )
    )

    config = await world.service.get_config(PARENT_ONLY)
    assert config.audience == "skjenke"
    # Every bar below is "own": one tab, and no "Andre" tab.
    assert [(t.id, t.label, t.scope) for t in config.tabs] == [
        ("wishes", "Ønsker", None),
        ("open", "Mine grupper", "own"),
        ("mine", "Mine", None),
    ]
    assert {g.group_id for g in config.groups} == {STJERNE, HALVTIMEN}
    assert all(g.is_member and not g.in_training for g in config.groups)
    assert all(g.quota_text == "Kontrakt: 1 skift i uka" for g in config.groups)
    await world.service.get_wishes(PARENT_ONLY)  # wishes are allowed

    listing = await world.service.list_open(PARENT_ONLY, scope="own", days=40)
    skjenker = place(listing, world.shift_ids["tue"], "skjenker")  # open for samfunnettjeneste
    assert skjenker is not None and not skjenker.counts_as_samfunnettjeneste
    # No barkurs needed for a Skjenke member, only brannkurs.
    assert [offer.course_key for offer in skjenker.needs_course_signup] == ["brann"]
    # Without a qualification row the closed places stay closed.
    assert place(listing, world.shift_ids["thu"], "skjenker") is None
    assert place(listing, world.shift_ids["tue"], "skiftleder") is None
    assert place(listing, world.shift_ids["fri"], "bartender") is not None

    # A member of one bar does not get the sibling as a role.
    sander = await world.service.get_config(SANDER)
    assert {g.group_id: g.is_member for g in sander.groups} == {STJERNE: True, HALVTIMEN: False}


GRANDPARENT, GRANDPARENT_ONLY = 5, 17


async def test_a_member_of_a_grandparent_group_gets_no_bar_and_no_skjenke_audience(world: World) -> None:
    await _add_parent_group(world)
    session = world.session
    await session.execute(
        insert(groups).values(id=GRANDPARENT, slug="rot", name="Rot", is_active=True, active_through_semester=20992)
    )
    await session.execute(update(groups).where(groups.c.id == PARENT).values(parent_group_id=GRANDPARENT))
    await session.execute(
        insert(volunteer_records).values(
            id=GRANDPARENT_ONLY, first_name="Gro", last_name="Grand", birth_date=date(1998, 4, 4), gender="A",
            created_at=world.clock.now,
        )
    )
    await session.execute(insert(assignment_roles).values(id=10, group_id=GRANDPARENT, name="Medlem", penguin_points=1))
    await session.execute(
        insert(role_assignments).values(
            volunteer_id=GRANDPARENT_ONLY, group_id=GRANDPARENT, role_id=10, semester=SEMESTER, contract_signed=True
        )
    )

    config = await world.service.get_config(GRANDPARENT_ONLY)
    assert config.audience != "skjenke"
    assert not any(g.is_member for g in config.groups)

    # In the same three-level tree, a member of the direct parent still gets the bars below it.
    await session.execute(
        insert(volunteer_records).values(
            id=PARENT_ONLY, first_name="Pia", last_name="Parent", birth_date=date(1998, 4, 4), gender="A",
            created_at=world.clock.now,
        )
    )
    await session.execute(insert(assignment_roles).values(id=9, group_id=PARENT, name="Medlem", penguin_points=1))
    await session.execute(
        insert(role_assignments).values(
            volunteer_id=PARENT_ONLY, group_id=PARENT, role_id=9, semester=SEMESTER, contract_signed=True
        )
    )
    direct = await world.service.get_config(PARENT_ONLY)
    assert direct.audience == "skjenke"
    assert {g.group_id for g in direct.groups if g.is_member} == {STJERNE, HALVTIMEN}


async def test_a_visitor_on_a_skiftleder_place_is_not_told_they_are_a_skiftleder_of_that_bar(world: World) -> None:
    await _add_parent_group(world)
    await world.session.execute(
        update(shift_group_settings).where(shift_group_settings.c.group_id == HALVTIMEN).values(
            sibling_skiftleder_allowed=True
        )
    )
    # Sigrid is skiftleder in Stjerne; the switch lets her take Halvtimen's skiftleder place.
    detail = await world.service.get_place(SIGRID, world.slots[("fri", "skiftleder")])
    texts = [r.text for r in detail.requirements]
    assert "Godkjent skiftleder" in texts
    assert not any(text.startswith("Skiftleder i") for text in texts)
    assert detail.place.can_claim


async def test_kokk_requirement_line_names_no_place(world: World) -> None:
    await world.session.execute(
        insert(volunteer_qualifications).values(
            volunteer_id=SANDER, qualification="kokk", status="approved", group_id=STJERNE, takes_trainees=False
        )
    )
    slot_id = (
        await world.session.execute(
            insert(shift_slots)
            .values(shift_id=world.shift_ids["thu"], kind="kokk", count=1, open_for_samf=False, sort_order=9)
            .returning(shift_slots.c.id)
        )
    ).scalar_one()
    detail = await world.service.get_place(SANDER, slot_id)
    assert "Godkjent kokk" in [r.text for r in detail.requirements]


async def test_the_semester_follows_the_services_own_clock(world: World) -> None:
    # Sander is a member in autumn 2026 only. In January 2027 the semester is 20271.
    assert (await world.service.shifts.eligibility_for(SANDER)).person.member_groups == {STJERNE}
    world.clock.now = oslo(date(2027, 1, 15), 9)
    assert (await world.service.shifts.eligibility_for(SANDER)).person.member_groups == frozenset()
    assert (await world.service._person_or_none(SANDER)).roles_by_group == {}
    assert (await world.service._person_or_none(SANDER)).semester == 20271
    # A row for 20271 makes him a member again, in both the board service and the app.
    await world.session.execute(
        insert(role_assignments).values(
            volunteer_id=SANDER, group_id=STJERNE, role_id=2, semester=20271, contract_signed=True
        )
    )
    assert (await world.service.shifts.eligibility_for(SANDER)).person.member_groups == {STJERNE}
    assert set((await world.service._person_or_none(SANDER)).roles_by_group) == {STJERNE}
