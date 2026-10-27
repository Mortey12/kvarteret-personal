"""Skiftlista for the app (kvarteret-internbevis), on the mobile-card session.

Mounted at ``/api/v1/mobile-card/shifts``. Every call takes
``Authorization: Bearer <mobile-card session token>`` like ``GET /me``,
and may return a renewed token in ``X-Mobile-Card-Session-Token``.

Errors are ``{"detail": "<Norwegian text>"}`` and the text is meant to be
shown as is: 401 bad session, 403 Skiftlista not switched on for the
person, 404 the shift or place is gone, 409 a rule says no.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict

from app.dependencies import get_mobile_card_service, get_shifts_app_service
from app.domain.mobile_card.errors import MobileCardInvalidAccessCodeError
from app.domain.mobile_card.models import MobileCardSessionIdentity
from app.domain.mobile_card.service import MobileCardService
from app.domain.shifts.app_models import (
    ClaimPlaceRequest,
    MyShiftsResponse,
    OpenScope,
    OpenShiftsResponse,
    PlaceDetailResponse,
    ShiftActionResponse,
    ShiftAppConfigResponse,
    ShiftCoursesResponse,
    ShiftWishesResponse,
    StatusMessageRequest,
    WishAnswerRequest,
)
from app.domain.shifts.app_service import MAX_OPEN_DAYS, ShiftsAppService
from app.domain.shifts.errors import ShiftAccessError, ShiftNotFoundError, ShiftPlacementError

RENEWED_TOKEN_HEADER = "X-Mobile-Card-Session-Token"


class ShiftErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str


_ERRORS = {
    401: {"model": ShiftErrorResponse, "description": "Missing, invalid or expired session."},
    403: {"model": ShiftErrorResponse, "description": "Skiftlista is not switched on for this person."},
}
_NOT_FOUND = {404: {"model": ShiftErrorResponse, "description": "The shift, place or course is gone."}}
_REFUSED = {409: {"model": ShiftErrorResponse, "description": "A rule refuses this; show detail as is."}}

router = APIRouter()


async def session_identity(
    response: Response,
    authorization: str | None = Header(default=None),
    service: MobileCardService = Depends(get_mobile_card_service),
) -> MobileCardSessionIdentity:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token.")
    try:
        identity = service.authenticate_session(authorization.split(" ", 1)[1])
    except MobileCardInvalidAccessCodeError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if identity.renewed_session_token:
        response.headers[RENEWED_TOKEN_HEADER] = identity.renewed_session_token
    return identity


Identity = Annotated[MobileCardSessionIdentity, Depends(session_identity)]
Service = Annotated[ShiftsAppService, Depends(get_shifts_app_service)]


def _volunteer_id(identity: MobileCardSessionIdentity) -> int:
    if identity.volunteer_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Skiftlista er bare for frivillige med internbevis.",
        )
    return identity.volunteer_id


@contextmanager
def _shift_errors() -> Iterator[None]:
    try:
        yield
    except ShiftAccessError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ShiftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ShiftPlacementError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# ---------------------------------------------------------------- config


@router.get(
    "/config",
    response_model=ShiftAppConfigResponse,
    operation_id="getShiftAppConfig",
    responses={401: _ERRORS[401]},
)
async def get_config(identity: Identity, service: Service) -> ShiftAppConfigResponse:
    """What the app shows: whether Skiftlista is on for this person, tabs, groups and labels.

    Never 403: a person without access gets ``enabled: false`` so the app hides the feature.
    """
    with _shift_errors():
        return await service.get_config(identity.volunteer_id)


# ---------------------------------------------------------------- open places


@router.get(
    "/open",
    response_model=OpenShiftsResponse,
    operation_id="listOpenShifts",
    responses=_ERRORS,
)
async def list_open(
    identity: Identity,
    service: Service,
    scope: OpenScope = "all",
    days: int = Query(default=14, ge=1, le=MAX_OPEN_DAYS),
) -> OpenShiftsResponse:
    """Places the person can see from now and ``days`` ahead, with why they can or cannot take each."""
    with _shift_errors():
        return await service.list_open(_volunteer_id(identity), scope=scope, days=days)


@router.get(
    "/places/{slot_id}",
    response_model=PlaceDetailResponse,
    operation_id="getShiftPlace",
    responses={**_ERRORS, **_NOT_FOUND},
)
async def get_place(identity: Identity, service: Service, slot_id: int) -> PlaceDetailResponse:
    """One place in detail: requirements, who else is on, and the claim button text."""
    with _shift_errors():
        return await service.get_place(_volunteer_id(identity), slot_id)


@router.post(
    "/places/{slot_id}/claim",
    response_model=ShiftActionResponse,
    operation_id="claimShiftPlace",
    responses={**_ERRORS, **_NOT_FOUND, **_REFUSED},
)
async def claim_place(
    identity: Identity,
    service: Service,
    slot_id: int,
    payload: Annotated[ClaimPlaceRequest | None, Body()] = None,
) -> ShiftActionResponse:
    """Take a place. Pass ``course_session_ids`` from ``needs_course_signup`` to sign up and claim in one step."""
    with _shift_errors():
        return await service.claim(_volunteer_id(identity), slot_id, payload or ClaimPlaceRequest())


# ---------------------------------------------------------------- my shifts


@router.get(
    "/mine",
    response_model=MyShiftsResponse,
    operation_id="listMyShifts",
    responses=_ERRORS,
)
async def list_mine(identity: Identity, service: Service) -> MyShiftsResponse:
    """The person's coming shifts, with this week's quota or samfunnettjeneste progress."""
    with _shift_errors():
        return await service.list_mine(_volunteer_id(identity))


@router.post(
    "/assignments/{assignment_id}/swap-request",
    response_model=ShiftActionResponse,
    operation_id="requestShiftSwap",
    responses={**_ERRORS, **_NOT_FOUND, **_REFUSED},
)
async def request_swap(
    identity: Identity,
    service: Service,
    assignment_id: int,
    payload: Annotated[StatusMessageRequest | None, Body()] = None,
) -> ShiftActionResponse:
    """Ask for someone to take over. The person stays on the shift until a leader moves someone in."""
    with _shift_errors():
        return await service.request_swap(
            _volunteer_id(identity), assignment_id, (payload or StatusMessageRequest()).message
        )


@router.delete(
    "/assignments/{assignment_id}/swap-request",
    response_model=ShiftActionResponse,
    operation_id="cancelShiftSwap",
    responses={**_ERRORS, **_NOT_FOUND, **_REFUSED},
)
async def cancel_swap(identity: Identity, service: Service, assignment_id: int) -> ShiftActionResponse:
    with _shift_errors():
        return await service.cancel_swap(_volunteer_id(identity), assignment_id)


@router.post(
    "/assignments/{assignment_id}/cannot-attend",
    response_model=ShiftActionResponse,
    operation_id="reportShiftCannotAttend",
    responses={**_ERRORS, **_NOT_FOUND, **_REFUSED},
)
async def report_cannot_attend(
    identity: Identity,
    service: Service,
    assignment_id: int,
    payload: Annotated[StatusMessageRequest | None, Body()] = None,
) -> ShiftActionResponse:
    """"Kan ikke i dag", on the day of the shift. The place opens to everyone; the person comes off when it is taken."""
    with _shift_errors():
        return await service.report_cannot_attend(
            _volunteer_id(identity), assignment_id, (payload or StatusMessageRequest()).message
        )


@router.delete(
    "/assignments/{assignment_id}/cannot-attend",
    response_model=ShiftActionResponse,
    operation_id="undoShiftCannotAttend",
    responses={**_ERRORS, **_NOT_FOUND, **_REFUSED},
)
async def undo_cannot_attend(identity: Identity, service: Service, assignment_id: int) -> ShiftActionResponse:
    with _shift_errors():
        return await service.undo_cannot_attend(_volunteer_id(identity), assignment_id)


# ---------------------------------------------------------------- wishes


@router.get(
    "/wishes",
    response_model=ShiftWishesResponse,
    operation_id="getShiftWishes",
    responses=_ERRORS,
)
async def get_wishes(identity: Identity, service: Service) -> ShiftWishesResponse:
    """Weekday answers for the semester, monthly status, and shifts that take wishes now.

    Only the person and the group's leaders see wishes.
    """
    with _shift_errors():
        return await service.get_wishes(_volunteer_id(identity))


@router.put(
    "/wishes/weekdays/{weekday}",
    response_model=ShiftWishesResponse,
    operation_id="setShiftWeekdayRule",
    responses={**_ERRORS, **_REFUSED},
)
async def set_weekday_rule(
    identity: Identity,
    service: Service,
    payload: WishAnswerRequest,
    weekday: int = Path(ge=0, le=6, description="0 = Monday ... 6 = Sunday"),
) -> ShiftWishesResponse:
    """Set or clear (``answer: null``) the answer for one weekday this semester."""
    with _shift_errors():
        return await service.set_weekday_rule(_volunteer_id(identity), weekday, payload.answer)


@router.put(
    "/wishes/shifts/{shift_id}",
    response_model=ShiftWishesResponse,
    operation_id="setShiftWish",
    responses={**_ERRORS, **_NOT_FOUND, **_REFUSED},
)
async def set_shift_wish(
    identity: Identity,
    service: Service,
    shift_id: int,
    payload: WishAnswerRequest,
) -> ShiftWishesResponse:
    """Override the weekday answer for one shift (``answer: null`` goes back to the weekday answer)."""
    with _shift_errors():
        return await service.set_shift_wish(_volunteer_id(identity), shift_id, payload.answer)


# ---------------------------------------------------------------- courses


@router.get(
    "/courses",
    response_model=ShiftCoursesResponse,
    operation_id="getShiftCourses",
    responses=_ERRORS,
)
async def get_courses(identity: Identity, service: Service) -> ShiftCoursesResponse:
    """Brannkurs (and barkurs for samfunnettjeneste): status, sign-up and coming dates."""
    with _shift_errors():
        return await service.get_courses(_volunteer_id(identity))


@router.post(
    "/courses/sessions/{session_id}/signup",
    response_model=ShiftCoursesResponse,
    operation_id="signUpForCourseSession",
    responses={**_ERRORS, **_NOT_FOUND, **_REFUSED},
)
async def sign_up(identity: Identity, service: Service, session_id: int) -> ShiftCoursesResponse:
    """Sign up for a session. An earlier sign-up for the same course is replaced."""
    with _shift_errors():
        return await service.sign_up(_volunteer_id(identity), session_id)


@router.delete(
    "/courses/sessions/{session_id}/signup",
    response_model=ShiftCoursesResponse,
    operation_id="leaveCourseSession",
    responses={**_ERRORS, **_NOT_FOUND, **_REFUSED},
)
async def leave_session(identity: Identity, service: Service, session_id: int) -> ShiftCoursesResponse:
    with _shift_errors():
        return await service.leave_session(_volunteer_id(identity), session_id)
