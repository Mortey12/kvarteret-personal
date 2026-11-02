"""Reads for placing people from the leader's week page (L2a): the picker and the person-first state.

The rules themselves are in ``planning`` (eligibility, age, absence) and are called through
``week_picker``. This module only loads what they need. It uses the service's own helpers
(``_load_slot`` for place and scope, ``_eligibility_rules`` for the group rules) instead of
repeating them, which is why it reaches for a few underscore names of ``ShiftsService``: both live
in this package and ``service.py`` is near its size cap, so the seams are not widened for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.domain.shifts.app_rules import effective_answer
from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import PoolPerson, ShiftView, SlotView
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.planning import (
    OSLO,
    VENUE_MIN_AGE,
    EligibilityContext,
    absent_to_replace,
    monday_of,
    open_for_everyone,
    open_places,
    person_eligibility,
    slot_kind,
)
from app.domain.shifts.week_picker import Candidate, PickerRow, PlaceFacts, picker_rows
from app.domain.shifts.week_view import day_label
from app.domain.shifts.wish_calendar import month_bounds, week_month
from app.shared.semester import get_current_semester_code


def week_bounds(week_start: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(week_start, time.min, tzinfo=OSLO),
        datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=OSLO),
    )


async def eligibility_for_people(service, volunteer_ids: list[int]) -> dict[int, EligibilityContext]:
    """Each person's eligibility context, built like ``ShiftsService._pool`` and ``eligibility_for``
    do, but for many people at once (three reads in all)."""
    repository = service.repository
    participating, parents, rules = await service._eligibility_rules()
    semester = get_current_semester_code(service._now())
    member_groups: dict[int, set[int]] = {}
    for member in await repository.list_shift_members(semester, sorted(participating)):
        member_groups.setdefault(member.volunteer_id, set()).add(member.group_id)
    rows: dict[int, list[tuple[str, str, int]]] = {}
    for record in await repository.list_qualifications(sorted(set(volunteer_ids))):
        rows.setdefault(record.volunteer_id, []).append(
            (record.qualification, record.status, record.group_id)
        )
    return {
        volunteer_id: EligibilityContext(
            person_eligibility(
                member_groups.get(volunteer_id, ()), rows.get(volunteer_id, ()), participating, parents
            ),
            rules,
        )
        for volunteer_id in volunteer_ids
    }


def make_candidates(
    pool: list[PoolPerson],
    contexts: dict[int, EligibilityContext],
    *,
    bar_id: int,
    answers: dict[tuple[int, int], str],
    shifts_per_day: dict[tuple[int, date], int],
    month_counts: dict[int, int],
    birth_dates: dict[int, date | None],
    swap_requests: set[int],
) -> list[Candidate]:
    """One ``Candidate`` per person of the pool. ``answers`` is (shift id, volunteer id) -> answer."""
    sl_token = f"{bar_id}:skiftleder:0"
    answers_by_person: dict[int, dict[int, str]] = {}
    for (shift_id, volunteer_id), answer in answers.items():
        answers_by_person.setdefault(volunteer_id, {})[shift_id] = answer
    days_by_person: dict[int, dict[date, int]] = {}
    for (volunteer_id, day), count in shifts_per_day.items():
        days_by_person.setdefault(volunteer_id, {})[day] = count
    return [
        Candidate(
            volunteer_id=person.volunteer_id,
            name=person.name,
            is_sl=sl_token in person.allowed_tokens,
            eligibility=contexts[person.volunteer_id],
            week_count=person.shifts_this_week,
            month_count=month_counts.get(person.volunteer_id, 0),
            answers=answers_by_person.get(person.volunteer_id, {}),
            shifts_per_day=days_by_person.get(person.volunteer_id, {}),
            birth_date=birth_dates.get(person.volunteer_id),
            swap_requested=person.volunteer_id in swap_requests,
        )
        for person in pool
    ]


def open_place_facts(days: list) -> list[PlaceFacts]:
    """Every open place of the shown days. The venue is the block's label, which equals the venue
    name for the venues with an age rule."""
    found: list[PlaceFacts] = []
    for block in days:
        for venue in block.venues:
            for shift in venue.shifts:
                on_shift = frozenset(
                    person.volunteer_id for slot in shift.slots for person in slot.people
                )
                found.extend(
                    PlaceFacts(
                        shift_id=shift.shift_id,
                        group_id=slot.group_id,
                        venue=venue.label,
                        day=block.day,
                        is_closed=shift.is_closed,
                        kind=slot.kind,
                        open_for_samf=slot.open_for_samf,
                        slot_id=slot.slot_id,
                        on_shift=on_shift,
                    )
                    for slot in _open_slots(shift)
                )
    return found


def _open_slots(shift: ShiftView) -> list[SlotView]:
    return [slot for slot in shift.slots if slot.open_places > 0 and not shift.is_closed]


def swap_requesters(days: list) -> set[int]:
    return {
        person.volunteer_id
        for block in days
        for venue in block.venues
        for shift in venue.shifts
        for slot in shift.slots
        for person in slot.people
        if person.swap_requested
    }


async def birth_dates_if_needed(service, volunteer_ids: list[int], places: list[PlaceFacts]):
    if not any(place.venue in VENUE_MIN_AGE for place in places):
        return {}
    return await service.repository.get_birth_dates(volunteer_ids)


@dataclass(slots=True)
class PickerView:
    slot_id: int
    shift_id: int
    group_id: int
    week_start: date
    title: str
    kind_label: str
    rows: list[PickerRow]
    replace_assignment_id: int | None = None
    replaced_name: str = ""
    # The "kan ikke i dag" person who comes off when someone takes this place.
    absent_name: str = ""


async def load_picker(
    service, *, slot_id: int, scope: GroupScope, replace_assignment_id: int | None = None
) -> PickerView:
    """The people who fit one place, for the picker. ``replace_assignment_id`` is the person
    being swapped out: the place is counted as if they had left, and they are not listed."""
    repository = service.repository
    shift, slot = await service._load_slot(slot_id, scope)
    replaced_id = None
    replaced_name = ""
    if replace_assignment_id is not None:
        old = await repository.get_assignment(replace_assignment_id)
        if old is None or old.slot_id != slot_id:
            raise ShiftNotFoundError("Plassen er endret. Oppdater siden.")
        replaced_id = old.volunteer_id
        replaced_name = await repository.get_volunteer_name(old.volunteer_id) or "Personen"
        slot.people = [p for p in slot.people if p.assignment_id != replace_assignment_id]
    if shift.is_closed:
        raise ShiftPlacementError("Skiftet er stengt.")
    if open_places(shift, slot) <= 0:
        raise ShiftPlacementError("Plassen er full.")
    absent = absent_to_replace(shift, slot)
    day = shift.starts_at.astimezone(OSLO).date()
    week_start = monday_of(day)
    plan = await service.get_week_plan(week_start, shift.group_id, scope=scope)
    pool = [person for person in plan.pool if person.volunteer_id != replaced_id]
    ids = [person.volunteer_id for person in pool]

    wishes = await repository.list_wishes_for([shift.shift_id], ids)
    answers = {key: ans for key, wish in wishes.items() if (ans := effective_answer(wish))}
    week_from, week_before = week_bounds(week_start)
    shifts_per_day: dict[tuple[int, date], int] = {}
    for record in await repository.list_shifts_between(week_from, week_before):
        record_day = record.starts_at.astimezone(OSLO).date()
        for volunteer_id in {p.volunteer_id for s in record.slots for p in s.people}:
            shifts_per_day[(volunteer_id, record_day)] = shifts_per_day.get((volunteer_id, record_day), 0) + 1
    month_from, month_before = month_bounds(week_month(week_start))
    month_counts = await repository.count_shifts_by_volunteer(month_from, month_before)
    place = PlaceFacts(
        shift_id=shift.shift_id,
        group_id=shift.group_id,
        venue=shift.venue,
        day=day,
        is_closed=shift.is_closed,
        kind=slot.kind,
        open_for_samf=open_for_everyone(slot),
        slot_id=slot.slot_id,
        on_shift=frozenset(p.volunteer_id for s in shift.slots for p in s.people),
    )
    people = make_candidates(
        pool,
        await eligibility_for_people(service, ids),
        bar_id=shift.group_id,
        answers=answers,
        shifts_per_day=shifts_per_day,
        month_counts=month_counts,
        birth_dates=await birth_dates_if_needed(service, ids, [place]),
        swap_requests=swap_requesters(plan.days),
    )
    kind = slot_kind(slot.kind).label.lower()
    where = f"{kind}, {day_label(day)}, {shift.title}"
    return PickerView(
        slot_id=slot.slot_id,
        shift_id=shift.shift_id,
        group_id=shift.group_id,
        week_start=week_start,
        title=f"Bytt ut {replaced_name} som {where}" if replaced_name else f"Sett inn som {where}",
        kind_label=kind,
        rows=picker_rows(people, place),
        replace_assignment_id=replace_assignment_id,
        replaced_name=replaced_name,
        absent_name=absent.name if absent else "",
    )
