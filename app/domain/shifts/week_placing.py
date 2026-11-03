"""Swapping a person from the week page (L2a): one request, one lock, no half-done swap.

Put and remove use ``ShiftsService.assign`` and ``remove`` as they are. A swap is not on the old
board, so it is built here from the same pieces ``assign`` uses: the pool check, the row lock, the
reload under the lock, ``_check`` (every hard rule and the warnings) and ``_take_place``. The old
person is taken out of the place in memory first, so the room is counted as if they had left;
nothing is written until every check has passed, then the old assignment is removed and the new one
added in the same request (one transaction). Warnings are returned, never raised.
"""

from __future__ import annotations

from app.domain.shifts.errors import ShiftNotFoundError
from app.domain.shifts.models import PlacementResult
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.service import NOT_IN_POOL, ShiftsService, _message
from app.domain.shifts.errors import ShiftPlacementError


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
