"""Adding and changing qualification types (see ``qualification_types.py``)."""

from __future__ import annotations

from app.domain.shifts.errors import ShiftPlacementError
from app.domain.shifts.qualification_types import KEY_PATTERN, STYLES, QualificationType
from app.domain.shifts.service import ShiftsService


async def add_type(
    service: ShiftsService, *, key: str, label: str, style: str, group_id: int | None
) -> str:
    key = key.strip().lower()
    label = label.strip()
    if not KEY_PATTERN.match(key):
        raise ShiftPlacementError("Nøkkelen må være små bokstaver, tall og _, og begynne med en bokstav (2 til 30 tegn).")
    if not label or len(label) > 60:
        raise ShiftPlacementError("Navnet må fylles ut (opptil 60 tegn).")
    if style not in STYLES:
        raise ShiftPlacementError("Ukjent type opplæring.")
    existing = await service.repository.list_qualification_types()
    if any(t.key == key for t in existing):
        raise ShiftPlacementError(f"Nøkkelen «{key}» finnes allerede.")
    await _check_group(service, group_id)
    await service.repository.add_qualification_type(
        key=key, label=label, style=style, group_id=group_id, sort_order=max((t.sort_order for t in existing), default=-1) + 1
    )
    return f"Opprettet «{label}»."


async def update_type(
    service: ShiftsService, type_id: int, *, label: str, style: str, active: bool, group_id: int | None
) -> str:
    label = label.strip()
    if not label or len(label) > 60:
        raise ShiftPlacementError("Navnet må fylles ut (opptil 60 tegn).")
    if style not in STYLES:
        raise ShiftPlacementError("Ukjent type opplæring.")
    await _check_group(service, group_id)
    found = await service.repository.update_qualification_type(
        type_id, label=label, style=style, active=active, group_id=group_id
    )
    if not found:
        raise ShiftPlacementError("Fant ikke kvalifikasjonen.")
    return f"Lagret «{label}»."


async def _check_group(service: ShiftsService, group_id: int | None) -> None:
    if group_id is not None and await service.repository.get_group_name(group_id) is None:
        raise ShiftPlacementError("Gruppen finnes ikke.")


async def list_types(service: ShiftsService) -> list[QualificationType]:
    return await service.repository.list_qualification_types()
