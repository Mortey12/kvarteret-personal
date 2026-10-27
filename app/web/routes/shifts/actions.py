"""Drag-and-drop writes for the shift plan (see static/js/shift-board.js).

Each action returns JSON ``{"message": ...}`` on success. A broken rule
is a 400 with the reason in ``detail``, which the board shows as is.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status

from app.dependencies import get_shift_scope, get_shifts_service, require_management_user
from app.domain.shifts.errors import (
    ShiftForbiddenError,
    ShiftNotFoundError,
    ShiftPlacementError,
)
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.models import PlacementResult
from app.domain.shifts.service import ShiftsService
from app.observability import log_admin_activity
from app.web.route_helpers import redirect_to

router = APIRouter()


def _result(result: PlacementResult) -> dict[str, str]:
    return {"message": result.message}


@router.post("/shifts/assignments")
async def shift_assignment_create(
    request: Request,
    slot_id: int = Form(...),
    volunteer_id: int = Form(...),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    try:
        result = await shifts_service.assign(
            slot_id=slot_id, volunteer_id=volunteer_id, scope=scope
        )
    except ShiftForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
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
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    try:
        result = await shifts_service.move(
            assignment_id=assignment_id, slot_id=slot_id, scope=scope
        )
    except ShiftForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
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
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    try:
        result = await shifts_service.remove(assignment_id=assignment_id, scope=scope)
    except ShiftForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
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


def _back(path: str, **params: str | int | None) -> str:
    query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
    return f"{path}?{query}" if query else path


@router.post("/shifts/settings/{group_id}")
async def shift_settings_save(
    request: Request,
    group_id: int,
    app_enabled: bool = Form(False),
    samf_enabled: bool = Form(False),
    meeting_point: str = Form(""),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    try:
        message = await shifts_service.save_group_settings(
            group_id,
            app_enabled=app_enabled,
            samf_enabled=samf_enabled,
            meeting_point=meeting_point,
            scope=scope,
        )
    except ShiftForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ShiftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ShiftPlacementError as exc:
        return redirect_to(_back("/shifts/settings", error=str(exc)))
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.settings",
        subject_type="group",
        subject_id=group_id,
        details={"app_enabled": app_enabled, "samf_enabled": samf_enabled},
    )
    return redirect_to(_back("/shifts/settings", notice=message))


@router.post("/shifts/weeks/status")
async def shift_week_status_save(
    request: Request,
    group_id: int = Form(...),
    week: date = Form(...),
    new_status: str = Form(..., alias="status"),
    whole_month: bool = Form(False),
    view_group: int | None = Form(None),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    board = "/shifts"
    try:
        message = await shifts_service.set_week_status(
            group_id=group_id, week=week, status=new_status, whole_month=whole_month, scope=scope
        )
    except ShiftForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ShiftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ShiftPlacementError as exc:
        return redirect_to(_back(board, week=week.isoformat(), group=view_group, error=str(exc)))
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.week_status",
        subject_type="group",
        subject_id=group_id,
        details={"week": week.isoformat(), "status": new_status, "whole_month": whole_month},
    )
    return redirect_to(_back(board, week=week.isoformat(), group=view_group, notice=message))
