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
    service: ShiftsService, *, assignment_id: int, slot_id: int, volunteer_id: int, scope: GroupScope
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
    warnings = await service._check(shift, slot, volunteer_id)
    await service._take_place(shift, slot)
    await repository.remove_assignment(assignment_id)
    await repository.add_assignment(slot.slot_id, volunteer_id)
    text = f"{name} er satt opp som {service._describe(shift, slot)}, i stedet for {old_name}."
    return PlacementResult(_message(text, warnings))
