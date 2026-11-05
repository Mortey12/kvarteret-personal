"""The page where a named editor adds and changes the qualifications a leader can train people for.

Anyone who manages shifts can read the list; only the e-mail addresses in
``TRAINING_COURSE_EDITORS`` can change it (the same few who may mark a course as registered by
training). See ``app/domain/shifts/qualification_types.py``.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status

from app.dependencies import get_settings, get_shifts_service, require_management_user
from app.domain.courses.training_courses import LOCKED_TEXT, may_edit_training_course
from app.domain.shifts.errors import ShiftPlacementError
from app.domain.shifts.qualification_types import STYLES
from app.domain.shifts.qualification_types_service import add_type, list_types, update_type
from app.domain.shifts.service import ShiftsService
from app.observability import log_admin_activity
from app.web.route_helpers import redirect_to
from app.web.templates import templates

router = APIRouter()

PATH = "/shifts/qualifications"


def _editor(user, settings) -> bool:
    return may_edit_training_course(getattr(user, "email", None), settings.training_course_editors)


def _back(**params: str) -> str:
    return f"{PATH}?{urlencode(params)}" if params else PATH


def _group_id(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


@router.get(PATH)
async def qualification_types_page(
    request: Request,
    notice: str | None = None,
    error: str | None = None,
    current_user=Depends(require_management_user),
    settings=Depends(get_settings),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    return templates.TemplateResponse(
        request,
        "pages/shifts/qualification_types.html",
        {
            "title": "Skiftlista: kvalifikasjoner",
            "section": "shifts",
            "current_user": current_user,
            "types": await list_types(shifts_service),
            "groups": await shifts_service.repository.list_shift_groups(),
            "styles": STYLES,
            "can_edit": _editor(current_user, settings),
            "locked_text": LOCKED_TEXT,
            "notice": notice,
            "error": error,
        },
    )


@router.post(PATH)
async def qualification_types_add(
    request: Request,
    key: str = Form(...),
    label: str = Form(...),
    style: str = Form(...),
    group_id: str = Form(default=""),
    current_user=Depends(require_management_user),
    settings=Depends(get_settings),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    if not _editor(current_user, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LOCKED_TEXT)
    try:
        message = await add_type(shifts_service, key=key, label=label, style=style, group_id=_group_id(group_id))
    except ShiftPlacementError as exc:
        return redirect_to(_back(error=str(exc)))
    log_admin_activity(request=request, user=current_user, action="qualification_type.create", subject_type="qualification_type", subject_id=0, details={"key": key.strip().lower()})
    return redirect_to(_back(notice=message))


@router.post(PATH + "/{type_id}")
async def qualification_types_update(
    request: Request,
    type_id: int,
    label: str = Form(...),
    style: str = Form(...),
    group_id: str = Form(default=""),
    active: str | None = Form(default=None),
    current_user=Depends(require_management_user),
    settings=Depends(get_settings),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    if not _editor(current_user, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LOCKED_TEXT)
    try:
        message = await update_type(
            shifts_service, type_id, label=label, style=style, active=bool(active), group_id=_group_id(group_id)
        )
    except ShiftPlacementError as exc:
        return redirect_to(_back(error=str(exc)))
    log_admin_activity(request=request, user=current_user, action="qualification_type.update", subject_type="qualification_type", subject_id=type_id, details={"active": bool(active)})
    return redirect_to(_back(notice=message))
