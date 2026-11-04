"""Swapping a person from the week page (L2a): one request, one lock, no half-done swap.

Put and remove use ``ShiftsService.assign`` and ``remove`` as they are. A swap is not on the old
board, so it is built here from the same pieces ``assign`` uses: the pool check, the row lock, the
reload under the lock, ``_check`` (every hard rule and the warnings) and ``_take_place``. The old
person is taken out of the place in memory first, so the room is counted as if they had left;
nothing is written until every check has passed, then the old assignment is removed and the new one
added in the same request (one transaction). Warnings are returned, never raised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import AssignedPerson, PlacementResult
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.planning import OSLO, monday_of, open_places
from app.domain.shifts.service import NOT_IN_POOL, ShiftsService, _message, _week_bounds
from app.shared.semester import get_current_semester_code


STALE_PLACE = "Plassen er endret. Oppdater siden."


async def remove_from_place(
    service: ShiftsService, *, assignment_id: int, slot_id: int, scope: GroupScope
) -> PlacementResult:
    """``ShiftsService.remove``, after checking that the assignment is on the place the page showed.

    The group for the scope check is the assignment's own shift (``remove`` loads it); the scope is
    checked before the place is compared, so a mismatch tells nothing about a place outside it.
    """
    assignment = await service.repository.get_assignment(assignment_id)
    if assignment is None:
        raise ShiftNotFoundError("Tildelingen finnes ikke lenger. Oppdater siden.")
    await service._load_slot(assignment.slot_id, scope)
    if assignment.slot_id != slot_id:
        raise ShiftNotFoundError(STALE_PLACE)
    return await service.remove(assignment_id=assignment_id, scope=scope)


async def replace_person(
    service: ShiftsService,
    *,
    assignment_id: int,
    slot_id: int,
    volunteer_id: int,
    scope: GroupScope,
    allow_unqualified: bool = False,
) -> PlacementResult:
    repository = service.repository
    old = await repository.get_assignment(assignment_id)
    if old is None:
        raise ShiftNotFoundError("Tildelingen finnes ikke lenger. Oppdater siden.")
    # First, as in assign: the same answer whether the person is outside the pool or does not exist.
    await service._require_in_pool(volunteer_id, scope)
    # The group for the scope check comes from the assignment's own shift, never from the request.
    shift, _ = await service._load_slot(old.slot_id, scope)
    if old.slot_id != slot_id:
        raise ShiftNotFoundError(STALE_PLACE)
    await service._lock(shift.shift_id)
    # Read again under the lock: the place may have changed meanwhile.
    old = await repository.get_assignment(assignment_id)
    if old is None:
        raise ShiftNotFoundError("Tildelingen finnes ikke lenger. Oppdater siden.")
    if old.slot_id != slot_id:
        raise ShiftNotFoundError(STALE_PLACE)
    shift, slot = await service._load_slot(old.slot_id, scope)
    if old.volunteer_id == volunteer_id:
        return PlacementResult("Ingen endring.")
    name = await repository.get_volunteer_name(volunteer_id)
    if name is None:
        raise ShiftPlacementError(NOT_IN_POOL)
    old_name = await repository.get_volunteer_name(old.volunteer_id) or "Personen"
    slot.people = [person for person in slot.people if person.assignment_id != assignment_id]
    warnings = await service._check(shift, slot, volunteer_id, allow_unqualified=allow_unqualified)
    await service._take_place(shift, slot)
    await repository.remove_assignment(assignment_id)
    await repository.add_assignment(slot.slot_id, volunteer_id)
    text = f"{name} er satt opp som {service._describe(shift, slot)}, i stedet for {old_name}."
    return PlacementResult(_message(text, warnings))


async def swap_people(
    service: ShiftsService,
    *,
    assignment_a: int,
    assignment_b: int,
    scope: GroupScope,
    allow_unqualified: bool = False,
) -> PlacementResult:
    """Two placed people change places, all or nothing.

    Built like ``replace_person``: scope first, both shifts locked in id order, everything read
    again under the lock, both people taken out of their places in memory so the room is counted as
    if they had left, ``_check`` for each person in the other's place, and only then the two moves.
    ``move_assignment`` keeps each assignment id. One request is one transaction, so a refusal for
    either person leaves both where they were.
    """
    repository = service.repository
    first = await repository.get_assignment(assignment_a)
    second = await repository.get_assignment(assignment_b)
    if first is None or second is None:
        raise ShiftNotFoundError("Tildelingen finnes ikke lenger. Oppdater siden.")
    if assignment_a == assignment_b:
        return PlacementResult("Ingen endring.")
    # Scope is checked on both places before anything else is read or locked.
    await service._load_slot(first.slot_id, scope)
    await service._load_slot(second.slot_id, scope)
    await service._lock(first.shift_id, second.shift_id)
    # Read again under the locks: the places may have changed meanwhile.
    first = await repository.get_assignment(assignment_a)
    second = await repository.get_assignment(assignment_b)
    if first is None or second is None:
        raise ShiftNotFoundError("Tildelingen finnes ikke lenger. Oppdater siden.")
    if first.slot_id == second.slot_id:
        return PlacementResult("Ingen endring.")
    shift_one, slot_one = await service._load_slot(first.slot_id, scope)
    if second.shift_id == first.shift_id:
        # One shift: one in-memory copy, so both places are taken out of the same record.
        shift_two = shift_one
        slot_two = next(item for item in shift_one.slots if item.slot_id == second.slot_id)
    else:
        shift_two, slot_two = await service._load_slot(second.slot_id, scope)
    name_one = await repository.get_volunteer_name(first.volunteer_id) or "Personen"
    name_two = await repository.get_volunteer_name(second.volunteer_id) or "Personen"

    for shift in {id(shift_one): shift_one, id(shift_two): shift_two}.values():
        for item in shift.slots:
            item.people = [
                person for person in item.people if person.assignment_id not in (assignment_a, assignment_b)
            ]
    warnings: list[str] = []
    for shift, slot, volunteer_id, moving, name in (
        (shift_two, slot_two, first.volunteer_id, assignment_a, name_one),
        (shift_one, slot_one, second.volunteer_id, assignment_b, name_two),
    ):
        try:
            warnings += await service._check(
                shift, slot, volunteer_id, moving_assignment_id=moving, allow_unqualified=allow_unqualified
            )
        except ShiftPlacementError as exc:
            raise ShiftPlacementError(f"{name}: {exc}") from exc
    await service._take_place(shift_two, slot_two)
    await service._take_place(shift_one, slot_one)
    await repository.move_assignment(assignment_a, second.slot_id)
    await repository.move_assignment(assignment_b, first.slot_id)
    return PlacementResult(_message(f"{name_one} og {name_two} har byttet plass.", list(dict.fromkeys(warnings))))


# ---- copy last week (item 7)

COPY_SKIP_LINES = 12  # skipped people named one by one; the rest are counted


@dataclass(slots=True)
class CopyResult:
    message: str
    placed: int
    # One line per skipped person (names are for the leader's eyes only) and soft warnings.
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _shift_key(shift) -> tuple:
    """Same shift one week earlier: same venue and title, same weekday and clock time in Oslo."""
    local = shift.starts_at.astimezone(OSLO)
    return (shift.venue, shift.title, local.weekday(), local.time())


async def copy_previous_week(
    service: ShiftsService, *, group_id: int, week: date, scope: GroupScope
) -> CopyResult:
    """Put last week's people on the same places this week, filling EMPTY places only.

    All or nothing in one request (the caller rolls back on any error); never removes anybody;
    every placement goes through ``ShiftsService._check`` (closed shift, already on the shift, the
    qualification, the venue's age limit), and what that refuses is skipped and reported, not
    forced. A person who answered "kan ikke" for this shift, who has left the pool, or who said
    "kan ikke i dag" last week is skipped. Shifts match by venue, title, weekday and clock time;
    places match by role, in the order last week's people stood in them.
    """
    repository = service.repository
    name = await repository.get_group_name(group_id)
    if name is None:
        raise ShiftNotFoundError("Gruppen finnes ikke.")
    scope.require(group_id, name)
    monday = monday_of(week)
    start, end = _week_bounds(monday)
    prev_start, prev_end = _week_bounds(monday - timedelta(days=7))
    previous = await repository.list_shifts_between(prev_start, prev_end, group_id)
    if not previous:
        return CopyResult("Ingen endring. Forrige uke har ingen skift å kopiere fra.", 0)
    target = await repository.list_shifts_between(start, end, group_id)
    # Lock, then read again: somebody may have placed people meanwhile.
    await service._lock(*[shift.shift_id for shift in target])
    target = await repository.list_shifts_between(start, end, group_id)

    previous_by_key = {_shift_key(shift): shift for shift in previous}
    members = await repository.list_shift_members(
        get_current_semester_code(service._now()), await service._pool_group_ids(scope)
    )
    pool = {member.volunteer_id for member in members}
    people_ids = sorted({p.volunteer_id for s in previous for slot in s.slots for p in slot.people})
    wishes = await repository.list_wishes_for([s.shift_id for s in target], people_ids)

    placed = 0
    no_room = 0
    skipped: list[str] = []
    notes: list[str] = []
    for shift in target:
        old = previous_by_key.get(_shift_key(shift))
        if old is None or shift.is_closed:
            continue
        for kind in dict.fromkeys(slot.kind for slot in shift.slots):
            if kind == "opplaering":
                continue  # training is a mode of a place, never copied
            for person in (p for slot in old.slots if slot.kind == kind for p in slot.people):
                if person.cannot_attend:
                    continue  # they did not work it last week
                if wishes.get((shift.shift_id, person.volunteer_id)) == "nei":
                    skipped.append(f"{person.name}: svarte «kan ikke» for {_describe_shift(shift)}.")
                    continue
                if person.volunteer_id not in pool:
                    skipped.append(f"{person.name}: er ikke lenger med i gruppa.")
                    continue
                slot = next(
                    (s for s in shift.slots if s.kind == kind and open_places(shift, s, count_absent=True) > 0),
                    None,
                )
                if slot is None:
                    no_room += 1
                    continue
                try:
                    warnings = await service._check(shift, slot, person.volunteer_id)
                except ShiftPlacementError as refused:
                    skipped.append(f"{person.name}, {_describe_shift(shift)}: {refused}")
                    continue
                new_id = await repository.add_assignment(slot.slot_id, person.volunteer_id)
                slot.people.append(
                    AssignedPerson(new_id, person.volunteer_id, person.name, False, first_name=person.first_name)
                )
                placed += 1
                notes.extend(w for w in warnings if w not in notes)

    lines = skipped[:COPY_SKIP_LINES]
    if len(skipped) > COPY_SKIP_LINES:
        lines.append(f"…og {len(skipped) - COPY_SKIP_LINES} til ble hoppet over.")
    if no_room:
        lines.append(f"{no_room} fikk ikke plass fordi plassene var tatt eller færre enn forrige uke.")
    previous_week = (monday - timedelta(days=7)).isocalendar().week
    skipped_total = len(skipped) + no_room
    if placed == 0:
        message = f"Ingen endring. Ingen ble kopiert fra uke {previous_week}."
    else:
        message = f"Kopierte {placed} plasser fra uke {previous_week}."
    if skipped_total:
        message += f" {skipped_total} ble ikke kopiert."
    return CopyResult(message, placed, lines, notes)


def _describe_shift(shift) -> str:
    local = shift.starts_at.astimezone(OSLO)
    return f"{shift.title.lower()} {_WEEKDAYS[local.weekday()]} {local.day}.{local.month}"


_WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]


# ---- accept suggestions

@dataclass(slots=True)
class AcceptResult:
    message: str
    placed: int
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


async def accept_suggestions(
    service: ShiftsService, *, group_id: int, week: date, scope: GroupScope
) -> AcceptResult:
    """Turn the pending suggestions of a bar's week into real placements.

    Each one goes through the same rules as a hand placement (``_check``: closed shift, already on
    the shift, qualification, the venue's age limit) and the place must still be empty. What a rule
    refuses is skipped and listed, never forced. Every suggestion is removed afterwards, placed or
    not, so none is left to block publishing. All or nothing: the caller rolls back on an error."""
    repository = service.repository
    name = await repository.get_group_name(group_id)
    if name is None:
        raise ShiftNotFoundError("Gruppen finnes ikke.")
    scope.require(group_id, name)
    pending = await service.list_suggestions(group_id, week)
    if not pending:
        return AcceptResult("Ingen endring. Det er ingen forslag å godta.", 0)
    await service._lock(*sorted({item.shift_id for item in pending}))
    pending = await service.list_suggestions(group_id, week)  # again, under the lock
    start, end = _week_bounds(monday_of(week))
    shifts_by_id = {s.shift_id: s for s in await repository.list_shifts_between(start, end, group_id)}
    members = await repository.list_shift_members(
        get_current_semester_code(service._now()), await service._pool_group_ids(scope)
    )
    pool = {member.volunteer_id for member in members}

    placed = 0
    skipped: list[str] = []
    notes: list[str] = []
    for item in pending:
        shift = shifts_by_id.get(item.shift_id)
        slot = next((s for s in shift.slots if s.slot_id == item.slot_id), None) if shift else None
        who = await repository.get_volunteer_name(item.volunteer_id) or "Personen"
        if shift is None or slot is None:
            skipped.append(f"{who}: plassen finnes ikke lenger.")
            continue
        if item.volunteer_id not in pool:
            skipped.append(f"{who}: er ikke lenger med i gruppa.")
            continue
        if open_places(shift, slot, count_absent=True) <= 0:
            skipped.append(f"{who}, {_describe_shift(shift)}: plassen er tatt.")
            continue
        try:
            warnings = await service._check(shift, slot, item.volunteer_id)
        except ShiftPlacementError as refused:
            skipped.append(f"{who}, {_describe_shift(shift)}: {refused}")
            continue
        new_id = await repository.add_assignment(slot.slot_id, item.volunteer_id)
        slot.people.append(AssignedPerson(new_id, item.volunteer_id, who, False))
        placed += 1
        notes.extend(w for w in warnings if w not in notes)
    await repository.delete_suggestions([item.suggestion_id for item in pending])

    message = f"Godtok {placed} forslag." if placed else "Ingen endring. Ingen forslag kunne godtas."
    if skipped:
        message += f" {len(skipped)} ble ikke godtatt."
    return AcceptResult(message, placed, skipped[:COPY_SKIP_LINES], notes)
