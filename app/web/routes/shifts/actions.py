"""Drag-and-drop writes for the shift plan (see static/js/shift-board.js).

Each action returns JSON ``{"message": ...}`` on success. A broken rule
is a 400 with the reason in ``detail``, which the board shows as is.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status

from app.dependencies import get_shifts_service, require_management_user
from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import PlacementResult
from app.domain.shifts.service import ShiftsService
from app.observability import log_admin_activity

router = APIRouter()


def _result(result: PlacementResult) -> dict[str, str]:
    return {"message": result.message}


@router.post("/shifts/assignments")
async def shift_assignment_create(
    request: Request,
    slot_id: int = Form(...),
    volunteer_id: int = Form(...),
    current_user=Depends(require_management_user),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    try:
        result = await shifts_service.assign(slot_id=slot_id, volunteer_id=volunteer_id)
    except ShiftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ShiftPlacementError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.assign",
        subject_type="shift_slot",
        subject_id=slot_id,
        details={"volunteer_id": volunteer_id},
    )
    return _result(result)


@router.post("/shifts/assignments/{assignment_id}/move")
async def shift_assignment_move(
    request: Request,
    assignment_id: int,
    slot_id: int = Form(...),
    current_user=Depends(require_management_user),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    try:
        result = await shifts_service.move(assignment_id=assignment_id, slot_id=slot_id)
    except ShiftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ShiftPlacementError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.move",
        subject_type="shift_assignment",
        subject_id=assignment_id,
        details={"slot_id": slot_id},
    )
    return _result(result)


@router.post("/shifts/assignments/{assignment_id}/delete")
async def shift_assignment_delete(
    request: Request,
    assignment_id: int,
    current_user=Depends(require_management_user),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    try:
        result = await shifts_service.remove(assignment_id=assignment_id)
    except ShiftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.unassign",
        subject_type="shift_assignment",
        subject_id=assignment_id,
    )
    return _result(result)
