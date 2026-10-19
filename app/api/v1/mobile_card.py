from __future__ import annotations

import logging
from datetime import datetime
from app.observability import get_logger

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.dependencies import get_mobile_card_service, get_rate_limiter
from app.db.rate_limit import RateLimiter, RateLimitExceeded
from app.domain.mobile_card.errors import MobileCardDeliveryError
from app.domain.mobile_card.service import (
    MobileCardCurrentCardResult,
    MobileCardDuplicatePersonError,
    MobileCardInvalidAccessCodeError,
    MobileCardPersonNotFoundError,
    MobileCardRateLimitedError,
    MobileCardResponse,
    MobileCardService,
)
from app.observability import (
    MOBILE_DIAGNOSTIC_EVENTS,
    client_ip_from_request,
    with_named_span,
)

logger = get_logger("app.audit")


class AccessCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class MobileCardSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    access_code: str


class MobileCardSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str
    card: MobileCardResponse
    refresh_token: str | None = None


class MobileCardSessionRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class MobileCardSessionLogoutEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_version: str | None = None
    auth_error_code: str | None = None
    auth_error_message: str | None = None
    auth_error_status: int | None = None
    cached_user_id: int | None = None
    event_name: Literal["credentials_missing_after_login", "session_invalidated"]
    execution_environment: str | None = None
    had_cached_user: bool
    had_login_marker: bool
    had_stored_credentials: bool
    occurred_at: str
    platform: str
    runtime_version: str | None = None
    update_channel: str | None = None
    update_id: str | None = None


class AcceptedStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted"]


router = APIRouter()


class MobileDiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: Literal[
        "cache_fallback_started",
        "cache_fallback_recovered",
        "credentials_missing_after_login",
        "logout_failed",
        "logout_succeeded",
        "response_invalid",
        "session_invalidated",
        "session_token_persist_failed",
    ]
    event_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    operation_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    attempt_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_:-]+$")
    attempt_no: int = Field(ge=1, le=100)
    source: Literal["client"]
    occurred_at: datetime
    platform: Literal["ios", "android", "web"]
    app_version: str | None = Field(default=None, max_length=64)
    runtime_version: str | None = Field(default=None, max_length=64)
    update_channel: str | None = Field(default=None, max_length=64)
    update_id: str | None = Field(default=None, max_length=128)
    auth_error_code: str | None = Field(
        default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    auth_error_status: int | None = Field(default=None, ge=400, le=599)
    had_cached_user: bool | None = None
    had_login_marker: bool | None = None
    had_stored_credentials: bool | None = None


@router.post(
    "/client-events/diagnostics",
    status_code=202,
    response_model=AcceptedStatusResponse,
    operation_id="logMobileCardDiagnostic",
)
async def log_client_diagnostic(
    request: Request,
    payload: MobileDiagnosticRequest,
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> AcceptedStatusResponse:
    # Signed-out clients must be able to report losing their credentials.
    # Bound this public ingestion path using the existing shared rate limiter.
    if len(await request.body()) > 8192:
        raise HTTPException(413, "Diagnostic payload too large.")
    try:
        await limiter.hit(
            f"mobile-diagnostics:{client_ip_from_request(request) or 'unknown'}",
            limit=60,
            window_seconds=60,
        )
    except RateLimitExceeded:
        raise HTTPException(429, "Too many diagnostics.") from None
    cache_event = payload.event_name.startswith("cache_fallback_")
    event = MOBILE_DIAGNOSTIC_EVENTS[payload.event_name]
    logger.log(
        logging.DEBUG
        if cache_event
        else logging.INFO
        if payload.event_name == "logout_succeeded"
        else logging.WARNING,
        event,
        extra={
            **payload.model_dump(exclude_none=True),
            "occurred_at": payload.occurred_at.isoformat(),
            "outcome": "succeeded"
            if payload.event_name == "logout_succeeded"
            else "failed",
            "failure_stage": "reauthorization"
            if payload.event_name == "session_invalidated"
            else payload.event_name,
        },
    )
    return AcceptedStatusResponse(status="accepted")


@router.post(
    "/access-codes",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedStatusResponse,
    operation_id="requestMobileCardAccessCode",
    responses={
        503: {
            "description": "Email delivery is temporarily unavailable. Check your inbox before retrying."
        }
    },
)
async def request_access_code(
    request: Request,
    payload: AccessCodeRequest,
    service: MobileCardService = Depends(get_mobile_card_service),
) -> AcceptedStatusResponse:
    try:
        with with_named_span("mobile_card.access_code.request"):
            await service.request_access_code(
                str(payload.email), source_key=client_ip_from_request(request)
            )
    except (MobileCardPersonNotFoundError, MobileCardDuplicatePersonError):
        return AcceptedStatusResponse(status="accepted")
    except MobileCardRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    except MobileCardDeliveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not confirm access code email delivery. Check your inbox before requesting another code.",
            headers={"Retry-After": "60"},
        ) from exc
    return AcceptedStatusResponse(status="accepted")


@router.post(
    "/sessions",
    response_model=MobileCardSessionResponse,
    response_model_exclude_none=True,
    operation_id="createMobileCardSession",
)
async def create_session(
    request: Request,
    payload: MobileCardSessionCreateRequest,
    # TODO(mobile-card-compat): Remove include_role_history compatibility gate once all supported mobile app versions use lenient backend-response Zod schemas. Additive backend fields must never be default-blocked after that.
    include_role_history: bool = False,
    service: MobileCardService = Depends(get_mobile_card_service),
) -> MobileCardSessionResponse:
    try:
        with with_named_span("mobile_card.session.create"):
            session = await service.create_session(
                str(payload.email),
                payload.access_code,
                include_role_history=include_role_history,
                source_key=client_ip_from_request(request),
            )
    except MobileCardInvalidAccessCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or access code.",
        ) from exc
    except MobileCardPersonNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or access code.",
        ) from exc
    except MobileCardRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    return MobileCardSessionResponse(
        session_token=session.session_token,
        card=session.card,
        refresh_token=session.refresh_token,
    )


@router.post(
    "/sessions/refresh",
    response_model=MobileCardSessionResponse,
    response_model_exclude_none=True,
    operation_id="refreshMobileCardSession",
)
async def refresh_session(
    payload: MobileCardSessionRefreshRequest,
    include_role_history: bool = False,
    service: MobileCardService = Depends(get_mobile_card_service),
) -> MobileCardSessionResponse:
    try:
        session = await service.refresh_session(
            payload.refresh_token, include_role_history=include_role_history
        )
    except MobileCardInvalidAccessCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    except MobileCardPersonNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is no longer valid.",
        ) from exc
    return MobileCardSessionResponse(
        session_token=session.session_token,
        card=session.card,
        refresh_token=session.refresh_token,
    )


@router.get(
    "/me",
    response_model=MobileCardResponse,
    response_model_exclude_none=True,
    operation_id="getCurrentMobileCard",
)
async def get_current_card(
    response: Response,
    authorization: str | None = Header(default=None),
    # TODO(mobile-card-compat): Remove include_role_history compatibility gate once all supported mobile app versions use lenient backend-response Zod schemas. Additive backend fields must never be default-blocked after that.
    include_role_history: bool = False,
    service: MobileCardService = Depends(get_mobile_card_service),
) -> MobileCardResponse:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token."
        )
    token = authorization.split(" ", 1)[1]
    try:
        card_result: MobileCardCurrentCardResult = await service.get_current_card(
            token, include_role_history=include_role_history
        )
    except MobileCardInvalidAccessCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    except MobileCardPersonNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is no longer valid.",
        ) from exc
    if card_result.renewed_session_token:
        response.headers["X-Mobile-Card-Session-Token"] = (
            card_result.renewed_session_token
        )
    return card_result.card


@router.post(
    "/client-events/session-logout",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedStatusResponse,
    operation_id="logMobileCardSessionLogoutEvent",
)
async def log_client_session_logout_event(
    request: Request,
    payload: MobileCardSessionLogoutEventRequest,
) -> AcceptedStatusResponse:
    logger.warning(
        MOBILE_DIAGNOSTIC_EVENTS[payload.event_name],
        extra={
            "event_name": payload.event_name,
            "platform": payload.platform,
            "app_version": payload.app_version,
            "auth_error_code": payload.auth_error_code,
            "auth_error_status": payload.auth_error_status,
            "had_cached_user": payload.had_cached_user,
            "had_login_marker": payload.had_login_marker,
            "had_stored_credentials": payload.had_stored_credentials,
        },
    )
    return AcceptedStatusResponse(status="accepted")
