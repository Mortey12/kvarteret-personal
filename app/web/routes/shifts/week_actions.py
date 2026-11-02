"""Placing people from the leader's week page (L2a): the picker, put, swap and remove.

Each action saves at once, one placement per request, through the same service methods and rules as
the old board (``assign``, ``remove``; the swap is ``week_placing.replace_person``). A broken rule or a
place taken meanwhile is not an error page: the answer is the refreshed week with a short message.
The page's JavaScript asks for that fragment with the header ``X-Week-Fragment``; without it
(no JavaScript) the action redirects back to the week page with ``notice`` or ``error``.
A group outside the leader's scope is a 403 before anything is read.
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
from app.domain.shifts.models import PlacementResult
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.week_page import load_week_page
from app.domain.shifts.week_picker import live_message
from app.domain.shifts.week_place_data import load_picker
from app.domain.shifts.week_placing import remove_from_place, replace_person
from app.observability import log_admin_activity
from app.web.route_helpers import redirect_to
from app.web.templates import templates

router = APIRouter()

FRAGMENT_HEADER = "x-week-fragment"


def _forbidden(exc: ShiftForbiddenError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _week_url(group: int, week: date, **message: str) -> str:
    return f"/shifts/week?{urlencode({'week': week.isoformat(), 'group': group, **message})}"


def _place_problems(view, slot_id: int, volunteer_id: int | None) -> list[str]:
    """The problem texts the refreshed grid shows on the person just placed."""
    if volunteer_id is None:
        return []
    for row in view.rows:
        for cell in row.cells:
            for place in cell.places if cell else []:
                if place.slot_id == slot_id and place.volunteer_id == volunteer_id:
                    return [problem.text for problem in place.problems]
    return []


def _level(view, slot_id: int, volunteer_id: int | None, failed: bool) -> str:
    if failed:
        return "error"
    for row in view.rows:
        for cell in row.cells:
            for place in cell.places if cell else []:
                if place.slot_id == slot_id and place.volunteer_id == volunteer_id:
                    return "warn" if place.problems else "ok"
    return "ok"


async def _answer(
    request: Request,
    service: ShiftsService,
    scope: GroupScope,
    *,
    group: int,
    week: date,
    slot_id: int,
    volunteer_id: int | None,
    message: str,
    failed: bool,
):
    """The refreshed week as a fragment (JavaScript) or a redirect back to the page."""
    if FRAGMENT_HEADER not in request.headers:
        key = "error" if failed else "notice"
        return redirect_to(_week_url(group, week, **{key: message}))
    try:
        page = await load_week_page(service, week=week, group_id=group, scope=scope)
    except ShiftForbiddenError as exc:
        raise _forbidden(exc) from exc
    if page.view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fann ikke uka.")
    text = message if failed else live_message(message, _place_problems(page.view, slot_id, volunteer_id))
    return templates.TemplateResponse(
        request,
        "pages/shifts/_week_refresh.html",
        {
            "page": page,
            "view": page.view,
            "message": f"Ikke lagret: {text}" if failed else text,
            "level": _level(page.view, slot_id, volunteer_id, failed),
            "focus_slot": slot_id,
            "focus_volunteer": volunteer_id or "",
        },
        status_code=status.HTTP_409_CONFLICT if failed else status.HTTP_200_OK,
    )


async def _run(call, *, request, service, scope, group, week, slot_id, volunteer_id, log):
    try:
        result: PlacementResult = await call
    except ShiftForbiddenError as exc:
        raise _forbidden(exc) from exc
    except (ShiftNotFoundError, ShiftPlacementError) as exc:
        return await _answer(
            request, service, scope, group=group, week=week, slot_id=slot_id,
            volunteer_id=volunteer_id, message=str(exc), failed=True,
        )
    log()
    return await _answer(
        request, service, scope, group=group, week=week, slot_id=slot_id,
        volunteer_id=volunteer_id, message=result.message, failed=False,
    )


@router.get("/shifts/week/picker")
async def week_picker(
    request: Request,
    slot_id: int,
    replace: int | None = None,
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    """The people who fit one place, as the content of the picker dialog."""
    try:
        picker = await load_picker(
            shifts_service, slot_id=slot_id, scope=scope, replace_assignment_id=replace
        )
    except ShiftForbiddenError as exc:
        raise _forbidden(exc) from exc
    except (ShiftNotFoundError, ShiftPlacementError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return templates.TemplateResponse(request, "pages/shifts/_week_picker.html", {"picker": picker})


@router.post("/shifts/week/place")
async def week_place(
    request: Request,
    slot_id: int = Form(...),
    volunteer_id: int = Form(...),
    group: int = Form(...),
    week: date = Form(...),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    return await _run(
        shifts_service.assign(slot_id=slot_id, volunteer_id=volunteer_id, scope=scope),
        request=request, service=shifts_service, scope=scope, group=group, week=week,
        slot_id=slot_id, volunteer_id=volunteer_id,
        log=lambda: log_admin_activity(
            request=request, user=current_user, action="shift.assign",
            subject_type="shift_slot", subject_id=slot_id,
            details={"volunteer_id": volunteer_id},
        ),
    )


@router.post("/shifts/week/replace")
async def week_replace(
    request: Request,
    assignment_id: int = Form(...),
    slot_id: int = Form(...),
    volunteer_id: int = Form(...),
    group: int = Form(...),
    week: date = Form(...),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    return await _run(
        replace_person(
            shifts_service, assignment_id=assignment_id, slot_id=slot_id, volunteer_id=volunteer_id, scope=scope
        ),
        request=request, service=shifts_service, scope=scope, group=group, week=week,
        slot_id=slot_id, volunteer_id=volunteer_id,
        log=lambda: log_admin_activity(
            request=request, user=current_user, action="shift.replace",
            subject_type="shift_assignment", subject_id=assignment_id,
            details={"volunteer_id": volunteer_id, "slot_id": slot_id},
        ),
    )


@router.post("/shifts/week/remove")
async def week_remove(
    request: Request,
    assignment_id: int = Form(...),
    slot_id: int = Form(...),
    group: int = Form(...),
    week: date = Form(...),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    return await _run(
        remove_from_place(shifts_service, assignment_id=assignment_id, slot_id=slot_id, scope=scope),
        request=request, service=shifts_service, scope=scope, group=group, week=week,
        slot_id=slot_id, volunteer_id=None,
        log=lambda: log_admin_activity(
            request=request, user=current_user, action="shift.unassign",
            subject_type="shift_assignment", subject_id=assignment_id,
        ),
    )
