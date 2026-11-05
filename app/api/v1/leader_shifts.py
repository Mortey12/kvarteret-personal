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
from app.db.session import rollback_request_session
from app.dependencies import (
    get_feedback_service,
    get_settings,
    get_shift_scope,
    get_shifts_service,
    require_management_user,
)
from app.domain.feedback.service import (
    FeedbackDeliveryError,
    FeedbackRateLimitedError,
    FeedbackService,
    FeedbackValidationError,
)
from app.domain.shifts.errors import ShiftForbiddenError, ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.leader_board import to_board
from app.domain.shifts.leader_models import (
    AddPlaceRequest,
    LeaderBar,
    LeaderMe,
    MoveRequest,
    Note,
    PlaceRequest,
    ProblemReportRequest,
    TrainingRequest,
    ProblemReportResult,
    ReplaceRequest,
    StatusRequest,
    SwapRequest,
    WeekBoard,
    WriteResult,
)
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.week_page import load_week_page
from app.domain.shifts.suggesting import generate_suggestions
from app.domain.shifts.training_service import (
    approve_training,
    extend_training,
    start_training,
    stop_training,
    training_views,
    types_for_bar,
)
from app.domain.shifts.week_placing import (
    accept_suggestions,
    copy_previous_week,
    remove_from_place,
    replace_person,
    swap_people,
)
from app.errors import NotConfiguredError
from app.observability import get_logger, log_admin_activity

router = APIRouter()
logger = get_logger("app.api.leader_shifts")

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
    weeks = [item.week_start for item in page.view.strip]
    changes = await service.week_changes(page.view.bar_id, [page.view.week_start, *weeks])
    suggestions = await service.list_suggestions(page.view.bar_id, page.view.week_start)
    trainings = await training_views(service, page.view.bar_id)
    return to_board(
        page.view,
        times=page.times,
        week_minutes=page.week_minutes,
        changes=changes,
        suggestions=suggestions,
        trainings=trainings,
        training_types=await types_for_bar(service, page.view.bar_id),
    )


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

NO_CHANGE = "Ingen endring"  # the service's own words for a request that asks for nothing new
WRONG_WEEK = "Endringen hører ikke til denne uka og er ikke lagret. Last uka på nytt og prøv igjen."
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
    marks_change: bool = True,
    warnings_for=None,
) -> JSONResponse:
    """The steps every write shares: the week must be the one the leader saw (else 409), the change
    runs under the service's own locks and rules, the audit log is written, and the answer is the
    refreshed week. ``run`` is a coroutine function returning a ``PlacementResult`` or a text;
    ``targets`` says which places to report problems for, as a function of the refreshed board;
    ``message_for(board)`` replaces the service's sentence where the leader app words it differently.
    A change to an explicitly published week is flagged "changed after publishing" with the leader's
    name (``marks_change`` is off for the status write itself, which publishes or reopens)."""
    # Writers to one bar's week go one at a time, and the version is compared only after the
    # lock is ours: the second of two leaders reads what the first did and gets the 409.
    # The lock lasts until the request commits.
    await service.lock_week(group_id, week_start)
    before = await _board(service, scope, week=week_start, group_id=group_id)
    if before.version != if_match:
        raise HTTPException(status.HTTP_409_CONFLICT, STALE_WEEK)
    try:
        result = await run()
    except ShiftForbiddenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except (ShiftNotFoundError, ShiftPlacementError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    message = result if isinstance(result, str) else result.message
    # The service puts its own hints after " Merk: "; the problems on the board say the same and more.
    message = message.partition(" Merk: ")[0].rstrip()
    # A request that changed nothing leaves a published week as it was.
    if marks_change and not message.startswith(NO_CHANGE):
        await service.mark_week_changed(group_id, week_start, user.display_name or user.username)
    log_admin_activity(
        request=request, user=user, action=action, subject_type=subject_type, subject_id=subject_id,
        details=details,
    )
    after = await _board(service, scope, week=week_start, group_id=group_id)
    if marks_change and after.version == before.version and not message.startswith(NO_CHANGE):
        # The change ran, but the week the leader is looking at did not change: the place or
        # person named in the request belongs to another bar or week. Undo it. This must be an
        # explicit rollback: the request session commits on any normal response, and a
        # deliberate 4xx from an endpoint is a normal response.
        await rollback_request_session()
        raise HTTPException(status.HTTP_409_CONFLICT, WRONG_WEEK)
    if message_for is not None:
        message = message_for(after)
    warnings = warnings_for(after) if warnings_for else (_problems_on(after, targets) if targets else [])
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
        action="shift.week_status", subject_type="shift_group", subject_id=group_id, marks_change=False,
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


@router.post(
    "/weeks/{week_start}/copy-previous",
    response_model=WriteResult,
    operation_id="copyLeaderShiftsPreviousWeek",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def copy_previous(
    request: Request,
    week_start: Annotated[date, Path(description="Any day in the week; the Monday is used.")],
    group_id: GroupId,
    if_match: IfMatch,
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """Fill the EMPTY places of this week with last week's people on the same places.

    All or nothing. Nobody is removed or replaced. People who answered "kan ikke", who have left the
    group, or whom a rule refuses (never forced) are skipped and listed in `warnings`."""
    outcome = {}

    async def run():
        try:
            result = await copy_previous_week(shifts_service, group_id=group_id, week=week_start, scope=scope)
        except Exception:
            # A deliberate 4xx commits what was written before it; nothing may stay half-copied.
            await rollback_request_session()
            raise
        outcome["result"] = result
        return result.message

    def warnings(_board):
        result = outcome["result"]
        return [Note(text=t, level="yellow") for t in [*result.skipped, *result.notes]]

    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action="shift.copy_week", subject_type="shift_group", subject_id=group_id,
        details={"week": week_start.isoformat(), "via": "leader-app"},
        run=run, warnings_for=warnings,
    )


@router.post(
    "/weeks/{week_start}/suggestions",
    response_model=WriteResult,
    operation_id="suggestLeaderShiftsWeek",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def suggest_week(
    request: Request,
    week_start: Annotated[date, Path(description="Any day in the week; the Monday is used.")],
    group_id: GroupId,
    if_match: IfMatch,
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """"Lag forslag for uka": suggestions for the empty places, from the wishes, the rules and
    fairness over the semester. Replaces the pending ones. Nobody is placed and volunteers see
    nothing; a leader accepts or discards (`/suggestions/accept`, `/suggestions/discard`)."""
    outcome = {}

    async def run():
        try:
            result = await generate_suggestions(shifts_service, group_id=group_id, week=week_start, scope=scope)
        except Exception:
            await rollback_request_session()
            raise
        outcome["result"] = result
        return result.message

    def warnings(_board):
        return [Note(text=text, level="yellow") for text in outcome["result"].notes]

    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action="shift.suggestions_generate", subject_type="shift_group", subject_id=group_id,
        details={"week": week_start.isoformat(), "via": "leader-app"},
        run=run, marks_change=False, warnings_for=warnings,
    )


@router.post(
    "/weeks/{week_start}/suggestions/accept",
    response_model=WriteResult,
    operation_id="acceptLeaderShiftsSuggestions",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def accept_week_suggestions(
    request: Request,
    week_start: Annotated[date, Path(description="Any day in the week; the Monday is used.")],
    group_id: GroupId,
    if_match: IfMatch,
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """"Godta forslag": the pending suggestions become real placements, each through the normal
    rules. What a rule refuses is skipped and listed in `warnings`. All or nothing."""
    outcome = {}

    async def run():
        try:
            result = await accept_suggestions(shifts_service, group_id=group_id, week=week_start, scope=scope)
        except Exception:
            await rollback_request_session()
            raise
        outcome["result"] = result
        return result.message

    def warnings(_board):
        result = outcome["result"]
        return [Note(text=t, level="yellow") for t in [*result.skipped, *result.notes]]

    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action="shift.suggestions_accept", subject_type="shift_group", subject_id=group_id,
        details={"week": week_start.isoformat(), "via": "leader-app"},
        run=run, warnings_for=warnings,
    )


@router.post(
    "/weeks/{week_start}/suggestions/discard",
    response_model=WriteResult,
    operation_id="discardLeaderShiftsSuggestions",
    dependencies=[Depends(require_leader_write)],
    responses=_WRITE_ERRORS,
)
async def discard_week_suggestions(
    request: Request,
    week_start: Annotated[date, Path(description="Any day in the week; the Monday is used.")],
    group_id: GroupId,
    if_match: IfMatch,
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> JSONResponse:
    """"Forkast forslag": throw the pending suggestions away. Nobody is placed, so a published week
    is not marked as changed."""
    async def run():
        count = await shifts_service.discard_suggestions(group_id=group_id, week=week_start, scope=scope)
        return f"Forkastet {count} forslag." if count else "Ingen endring. Det er ingen forslag å forkaste."

    return await _write(
        request, shifts_service, scope, current_user,
        group_id=group_id, week_start=week_start, if_match=if_match,
        action="shift.suggestions_discard", subject_type="shift_group", subject_id=group_id,
        details={"week": week_start.isoformat(), "via": "leader-app"},
        run=run, marks_change=False,
    )

def _training_endpoint(name: str, action: str, doer, summary: str):
    """One write on a person's training. Same guards as every leader write: Origin, CSRF, the
    week's version, the week lock, the audit trail."""

    @router.post(
        f"/weeks/{{week_start}}/training/{name}",
        response_model=WriteResult,
        operation_id=f"{name}LeaderShiftsTraining",
        dependencies=[Depends(require_leader_write)],
        responses=_WRITE_ERRORS,
        summary=summary,
    )
    async def endpoint(
        request: Request,
        week_start: Annotated[date, Path(description="Any day in the week; the Monday is used.")],
        group_id: GroupId,
        if_match: IfMatch,
        body: TrainingRequest,
        current_user: AuthenticatedUser = Depends(require_management_user),
        scope: GroupScope = Depends(get_shift_scope),
        shifts_service: ShiftsService = Depends(get_shifts_service),
    ) -> JSONResponse:
        async def run():
            try:
                return await doer(
                    shifts_service,
                    group_id=group_id,
                    volunteer_id=body.volunteer_id,
                    qualification=body.qualification,
                    scope=scope,
                    **(
                        {"approved_by": current_user.auth_user_id, "register_courses": body.register_courses}
                        if name == "approve"
                        else {}
                    ),
                )
            except Exception:
                await rollback_request_session()
                raise

        return await _write(
            request, shifts_service, scope, current_user,
            group_id=group_id, week_start=week_start, if_match=if_match,
            action=action, subject_type="volunteer", subject_id=body.volunteer_id,
            details={"week": week_start.isoformat(), "qualification": body.qualification, "via": "leader-app"},
            run=run, marks_change=False,
        )

    return endpoint


start_training_endpoint = _training_endpoint("start", "shift.training_start", start_training, "Put a person in training")
approve_training_endpoint = _training_endpoint("approve", "shift.training_approve", approve_training, "Approve a finished training")
extend_training_endpoint = _training_endpoint("extend", "shift.training_extend", extend_training, "Give a trainee one more shift")
stop_training_endpoint = _training_endpoint("stop", "shift.training_stop", stop_training, "Take a person out of training")



REPORT_NOT_SENT = "Feilmeldingen ble ikke sendt. Si fra til etjenesten på en annen måte."


@router.post(
    "/problem-reports",
    response_model=ProblemReportResult,
    operation_id="reportLeaderShiftsProblem",
    dependencies=[Depends(require_leader_write)],
    responses={
        **_ERRORS,
        422: {"model": ErrorResponse, "description": "The text is empty or too long."},
        429: {"model": ErrorResponse, "description": "Too many reports."},
        503: {"model": ErrorResponse, "description": "Reports are not set up on this server."},
    },
)
async def report_problem(
    body: Annotated[ProblemReportRequest, Body()],
    current_user: AuthenticatedUser = Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    feedback: FeedbackService = Depends(get_feedback_service),
    settings=Depends(get_settings),
) -> JSONResponse:
    """"Meld feil": becomes a bug issue in the same triage as the other apps' feedback.

    The issue carries who sent it (the account id, not a name), where they were (bar and week) and
    the environment, so a report from a rehearsal server is easy to tell from a real one. Nothing
    else from the board is added: the leader's own text is the only free text, and the app tells
    them not to write names in it. If reports are not configured here (no Linear key) the answer is
    503, so nobody believes it was sent."""
    if body.group_id is not None and not scope.allows(body.group_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Du har ikke tilgang til denne gruppa.")
    environment = settings.app_env or "ukjent"
    where = f"bar {body.group_id}" if body.group_id is not None else "ingen bar valgt"
    if body.week_start is not None:
        where += f", uke fra {body.week_start.isoformat()}"
    account = current_user.user_account_id
    try:
        await feedback.submit_feedback(
            feedback_type="bug",
            name=None,
            email=None,
            message=f"[Skiftlista, {environment}, {body.data_source}] {body.message}",
            page=f"Skiftlista, {where}",
            platform=f"skiftlista-app {body.app_version} ({environment})",
            user_id=account,
            source="skiftlista",
            source_key=f"leader:{account or 'unknown'}",
        )
    except FeedbackRateLimitedError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Du har sendt mange meldinger. Vent litt.") from exc
    except FeedbackValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FeedbackDeliveryError as exc:
        # Metadata only: the leader's text is not logged.
        logger.warning(
            "leader.problem_report.undelivered",
            extra={"environment": environment, "group_id": body.group_id, "data_source": body.data_source},
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, REPORT_NOT_SENT) from exc
    body_out = ProblemReportResult(message="Takk! Feilmeldingen er sendt til etjenesten.")
    return JSONResponse(content=body_out.model_dump(mode="json"), headers=NO_STORE)
