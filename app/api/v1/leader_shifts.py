"""Skiftlista for bar leaders, read side, on the web session cookie.

Mounted at ``/api/v1/leader/shifts``. Used by the separate leader web app. The caller
must be signed in with Personal's session cookie and be Admin or Gruppeadmin; a
Gruppeadmin sees only the bars in their scope (``get_shift_scope``). Errors are
``{"detail": "<Norwegian text>"}`` meant to be shown as is.

Cookie-authenticated writes under ``/api/`` skip Personal's CSRF check, so write
endpoints (not here yet) must add their own; ``GET /me`` hands out the token for it.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth.models import AuthenticatedUser
from app.dependencies import get_shift_scope, get_shifts_service, require_management_user
from app.domain.shifts.errors import ShiftForbiddenError
from app.domain.shifts.leader_board import to_board
from app.domain.shifts.leader_models import LeaderBar, LeaderMe, WeekBoard
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.week_page import load_week_page
from app.errors import NotConfiguredError

router = APIRouter()

NO_STORE = {"Cache-Control": "private, no-store", "Vary": "Cookie"}


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str


_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not signed in."},
    403: {"model": ErrorResponse, "description": "Not Admin or Gruppeadmin, or the bar is outside the scope."},
}


@router.get(
    "/me",
    response_model=LeaderMe,
    operation_id="getLeaderShiftsMe",
    responses=_ERRORS,
)
async def get_me(
    request: Request,
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """Who is signed in, the CSRF token for later writes, and the bars they may plan."""
    bars = [
        LeaderBar(id=bar.group_id, name=bar.name)
        for bar in await shifts_service.repository.list_shift_groups()
        if scope.allows(bar.group_id)
    ]
    body = LeaderMe(
        name=current_user.display_name or current_user.username,
        role=current_user.role.value,
        csrf_token=request.state.csrf_token,
        bars=bars,
    )
    return JSONResponse(content=body.model_dump(mode="json"), headers=NO_STORE)


@router.get(
    "/weeks/{week_start}",
    response_model=WeekBoard,
    operation_id="getLeaderShiftsWeek",
    responses={
        **_ERRORS,
        404: {"model": ErrorResponse, "description": "No bar in the caller's scope."},
        503: {"model": ErrorResponse, "description": "Skiftlista is not set up yet."},
    },
)
async def get_week(
    week_start: Annotated[date, Path(description="Any day in the week; the Monday is used.")],
    group_id: Annotated[int | None, Query(description="The bar. Default: the first bar in scope.")] = None,
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """One bar and one week: the grid, the people, and for every person which open places fit."""
    try:
        page = await load_week_page(
            shifts_service, week=week_start, group_id=group_id, scope=scope, allow_unqualified=True
        )
    except ShiftForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except NotConfiguredError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Skiftplanen er ikke satt opp ennå.") from None
    if page.view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Du har ingen barer å planlegge skift for.")
    return JSONResponse(content=to_board(page.view, times=page.times, week_minutes=page.week_minutes).model_dump(mode="json"), headers=NO_STORE)
