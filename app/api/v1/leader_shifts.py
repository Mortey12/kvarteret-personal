"""Skiftlista for bar leaders, on the web session cookie.

Mounted at ``/api/v1/leader/shifts``. Used by the separate leader web app. The caller
must be signed in with Personal's session cookie and be Admin or Gruppeadmin; a
Gruppeadmin sees and changes only the bars in their scope (``get_shift_scope``). Errors are
``{"detail": "<Norwegian text>"}`` meant to be shown as is.

Reads are ``GET /me`` and ``GET /weeks/{week_start}``. Writes (place, replace, move, swap, remove,
week status) reuse the service methods and rules of the server-rendered week page, and add what a
cookie-authenticated JSON route needs: ``leader_write_guard`` (Origin and CSRF), the week's
``If-Match`` version, and an explicit ``confirmed`` for a person who lacks the qualification.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.v1.leader_write_guard import require_leader_write
from app.auth.models import AuthenticatedUser
from app.dependencies import get_shift_scope, get_shifts_service, require_management_user
from app.domain.shifts.errors import ShiftForbiddenError, ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.leader_board import to_board
from app.domain.shifts.leader_models import (
    AddPlaceRequest,
    LeaderBar,
    LeaderMe,
    MoveRequest,
    Note,
    PlaceRequest,
    ReplaceRequest,
    StatusRequest,
    SwapRequest,
    WeekBoard,
    WriteResult,
)
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.week_page import load_week_page
from app.domain.shifts.week_placing import remove_from_place, replace_person, swap_people
from app.errors import NotConfiguredError
from app.observability import log_admin_activity

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


async def _board(
    service: ShiftsService, scope: GroupScope, *, week: date, group_id: int | None
) -> WeekBoard:
    """The week as the leader sees it now. Refusals are the same for reads and writes."""
    try:
        page = await load_week_page(service, week=week, group_id=group_id, scope=scope, allow_unqualified=True)
    except ShiftForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except NotConfiguredError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Skiftplanen er ikke satt opp ennå.") from None
    if page.view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Du har ingen barer å planlegge skift for.")
    return to_board(page.view, times=page.times, week_minutes=page.week_minutes)


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
    board = await _board(shifts_service, scope, week=week_start, group_id=group_id)
    return JSONResponse(content=board.model_dump(mode="json"), headers=NO_STORE)


# ---- writes -----------------------------------------------------------------------------------

STALE_WEEK = "En annen leder har endret uka. Siden er oppdatert, prøv igjen."


def _action(name: str, confirmed: bool) -> str:
    """The audit action. The log keeps only a fixed set of fields (no person ids, on purpose), so
    "the leader confirmed the warning" is written into the action name itself."""
    return f"{name}.confirmed" if confirmed else name

GroupId = Annotated[int, Query(description="The bar the leader is looking at.")]
WeekStart = Annotated[date, Query(description="The week the leader is looking at (any day in it).")]
IfMatch = Annotated[str, Header(alias="If-Match", description="The `version` of the board the leader saw.")]

_WRITE_ERRORS = {
    **_ERRORS,
    409: {"model": ErrorResponse, "description": "A rule refuses this, or the week changed; show detail as is."},
}


def _problems_on(board: WeekBoard, targets: list[tuple[int, int]]) -> list[Note]:
    """Problems of the people just placed, on the places they now hold (slot id, volunteer id)."""
    found: list[Note] = []
    for shift in board.shifts:
        for place in shift.places:
            if place.assignment and (place.slot_id, place.assignment.volunteer_id) in targets:
                for note in place.assignment.problems:
                    if note not in found:
                        found.append(note)
    return found


async def _write(
    request: Request,
    service: ShiftsService,
    scope: GroupScope,
    user: AuthenticatedUser,
    *,
    group_id: int,
    week_start: date,
    if_match: str,
    action: str,
    subject_type: str,
    subject_id: int,
    details: dict,
    run,
    targets=None,
    message_for=None,
) -> JSONResponse:
    """The steps every write shares: the week must be the one the leader saw (else 409), the change
    runs under the service's own locks and rules, the audit log is written, and the answer is the
    refreshed week. ``run`` is a coroutine function returning a ``PlacementResult`` or a text;
    ``targets`` says which places to report problems for, as a function of the refreshed board;
    ``message_for(board)`` replaces the service's sentence where the leader app words it differently."""
    before = await _board(service, scope, week=week_start, group_id=group_id)
    if before.version != if_match:
        raise HTTPException(status.HTTP_409_CONFLICT, STALE_WEEK)
    try:
        result = await run()
    except ShiftForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except (ShiftNotFoundError, ShiftPlacementError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    log_admin_activity(
        request=request, user=user, action=action, subject_type=subject_type, subject_id=subject_id,
        details=details,
    )
    message = result if isinstance(result, str) else result.message
    # The service puts its own hints after " Merk: "; the problems on the board say the same and more.
    message = message.partition(" Merk: ")[0].rstrip()
    after = await _board(service, scope, week=week_start, group_id=group_id)
    if message_for is not None:
        message = message_for(after)
    warnings = _problems_on(after, targets) if targets else []
    body = WriteResult(message=message, warnings=warnings, board=after)
    return JSONResponse(content=body.model_dump(mode="json"), headers=NO_STORE)


@router.post(
    "/placements",
    response_model=WriteResult,
    operation_id="placeLeaderShiftsPerson",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def place_person(
    request: Request,
    group_id: GroupId,
    week_start: WeekStart,
    if_match: IfMatch,
    body: Annotated[PlaceRequest, Body()],
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """Put a person on an empty place. `confirmed` is needed when they lack what the place needs."""
    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action=_action("shift.assign", body.confirmed), subject_type="shift_slot", subject_id=body.slot_id,
        details={"volunteer_id": body.volunteer_id, "confirmed": body.confirmed, "via": "leader-app"},
        run=lambda: shifts_service.assign(
            slot_id=body.slot_id, volunteer_id=body.volunteer_id, scope=scope, allow_unqualified=body.confirmed
        ),
        targets=[(body.slot_id, body.volunteer_id)],
    )


@router.put(
    "/placements/{assignment_id}",
    response_model=WriteResult,
    operation_id="replaceLeaderShiftsPerson",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def replace_placed_person(
    request: Request,
    assignment_id: int,
    group_id: GroupId,
    week_start: WeekStart,
    if_match: IfMatch,
    body: Annotated[ReplaceRequest, Body()],
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """Another person takes the place of the one on it."""
    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action=_action("shift.replace", body.confirmed), subject_type="shift_assignment", subject_id=assignment_id,
        details={
            "volunteer_id": body.volunteer_id, "slot_id": body.slot_id,
            "confirmed": body.confirmed, "via": "leader-app",
        },
        run=lambda: replace_person(
            shifts_service, assignment_id=assignment_id, slot_id=body.slot_id,
            volunteer_id=body.volunteer_id, scope=scope, allow_unqualified=body.confirmed,
        ),
        targets=[(body.slot_id, body.volunteer_id)],
    )


@router.post(
    "/placements/{assignment_id}/move",
    response_model=WriteResult,
    operation_id="moveLeaderShiftsPerson",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def move_placed_person(
    request: Request,
    assignment_id: int,
    group_id: GroupId,
    week_start: WeekStart,
    if_match: IfMatch,
    body: Annotated[MoveRequest, Body()],
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """Move a placed person to another empty place."""
    moved = await shifts_service.repository.get_assignment(assignment_id)
    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action=_action("shift.move", body.confirmed), subject_type="shift_assignment", subject_id=assignment_id,
        details={"slot_id": body.slot_id, "confirmed": body.confirmed, "via": "leader-app"},
        run=lambda: shifts_service.move(
            assignment_id=assignment_id, slot_id=body.slot_id, scope=scope, allow_unqualified=body.confirmed
        ),
        targets=[(body.slot_id, moved.volunteer_id)] if moved else None,
    )


@router.post(
    "/placements/swap",
    response_model=WriteResult,
    operation_id="swapLeaderShiftsPeople",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def swap_placed_people(
    request: Request,
    group_id: GroupId,
    week_start: WeekStart,
    if_match: IfMatch,
    body: Annotated[SwapRequest, Body()],
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """Two placed people change places, all or nothing."""
    first = await shifts_service.repository.get_assignment(body.assignment_a)
    second = await shifts_service.repository.get_assignment(body.assignment_b)
    targets = (
        [(second.slot_id, first.volunteer_id), (first.slot_id, second.volunteer_id)] if first and second else None
    )
    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action=_action("shift.swap", body.confirmed), subject_type="shift_assignment", subject_id=body.assignment_a,
        details={"with_assignment_id": body.assignment_b, "confirmed": body.confirmed, "via": "leader-app"},
        run=lambda: swap_people(
            shifts_service, assignment_a=body.assignment_a, assignment_b=body.assignment_b,
            scope=scope, allow_unqualified=body.confirmed,
        ),
        targets=targets,
    )


@router.delete(
    "/placements/{assignment_id}",
    response_model=WriteResult,
    operation_id="removeLeaderShiftsPerson",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def remove_placed_person(
    request: Request,
    assignment_id: int,
    slot_id: Annotated[int, Query(description="The place the page showed.")],
    group_id: GroupId,
    week_start: WeekStart,
    if_match: IfMatch,
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """Take a person off a place."""
    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action="shift.remove", subject_type="shift_assignment", subject_id=assignment_id,
        details={"slot_id": slot_id, "via": "leader-app"},
        run=lambda: remove_from_place(shifts_service, assignment_id=assignment_id, slot_id=slot_id, scope=scope),
    )


@router.post(
    "/shifts/{shift_id}/places",
    response_model=WriteResult,
    operation_id="addLeaderShiftsPlace",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def add_place(
    request: Request,
    shift_id: int,
    group_id: GroupId,
    week_start: WeekStart,
    if_match: IfMatch,
    body: Annotated[AddPlaceRequest, Body()],
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """One more place of a kind on a shift ("+"). Refused on a closed shift and above 20 of a kind."""
    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action="shift.place.add", subject_type="shift", subject_id=shift_id,
        details={"kind": body.kind, "via": "leader-app"},
        run=lambda: shifts_service.add_place(shift_id=shift_id, kind=body.kind, scope=scope),
    )


@router.delete(
    "/slots/{slot_id}/seat",
    response_model=WriteResult,
    operation_id="removeLeaderShiftsPlace",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def remove_place(
    request: Request,
    slot_id: int,
    group_id: GroupId,
    week_start: WeekStart,
    if_match: IfMatch,
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """One place less ("−"): removes one EMPTY seat of the slot, never a person."""
    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action="shift.place.remove", subject_type="shift_slot", subject_id=slot_id,
        details={"via": "leader-app"},
        run=lambda: shifts_service.remove_place(slot_id=slot_id, scope=scope),
    )


@router.put(
    "/weeks/{week_start}/status",
    response_model=WriteResult,
    operation_id="setLeaderShiftsWeekStatus",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def set_week_status(
    request: Request,
    week_start: Annotated[date, Path(description="Any day in the week; the Monday is used.")],
    group_id: GroupId,
    if_match: IfMatch,
    body: Annotated[StatusRequest, Body()],
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """Publish the week, or make it a draft again (only a coming week can be reopened)."""
    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action="shift.week_status", subject_type="shift_group", subject_id=group_id,
        details={"week": week_start.isoformat(), "status": body.status, "via": "leader-app"},
        run=lambda: shifts_service.set_week_status(
            group_id=group_id, week=week_start, status=body.status, whole_month=False, scope=scope
        ),
        # The service says "fordelt" for a published week (its own word); the page says "Publisert".
        message_for=lambda board: (
            f"Uke {board.week_number} for {board.bar.name}: "
            + ("publisert. Folk ser skiftene sine i appen nå." if board.status == "published" else "åpen for ønsker.")
        ),
    )
