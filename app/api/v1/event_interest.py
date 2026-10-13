from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.db.rate_limit import RateLimiter, RateLimitExceeded
from app.dependencies import get_event_interest_service, get_rate_limiter, get_settings
from app.domain.event_interest.service import EventInterestService

router = APIRouter(
    responses={
        401: {"description": "Missing, invalid or stale server signature"},
        413: {"description": "Request exceeds the bounded payload size"},
        429: {"description": "Replayed nonce or request limit exceeded"},
        503: {"description": "Event response signing is not configured"},
    }
)
signature_header = APIKeyHeader(
    name="X-Kvarteret-Signature", scheme_name="EventInterestHmac", auto_error=False
)


async def require_signed_event_interest(
    request: Request,
    signature: Annotated[str | None, Security(signature_header)] = None,
    settings: Settings = Depends(get_settings),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:
    secrets = [s for s in settings.signing_secrets if len(s) >= 32]
    if not secrets:
        raise HTTPException(503, "Event responses are unavailable")
    timestamp = request.headers.get("X-Kvarteret-Timestamp", "")
    nonce = request.headers.get("X-Kvarteret-Nonce", "")
    if (
        not re.fullmatch(r"[0-9]{10}", timestamp)
        or abs(int(time.time()) - int(timestamp)) > 300
    ):
        raise HTTPException(401, "Invalid signature")
    try:
        if str(UUID(nonce)) != nonce:
            raise ValueError
    except ValueError:
        raise HTTPException(401, "Invalid signature") from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024:
            raise HTTPException(413, "Request too large")
    # Cache the bounded body for FastAPI's model parser.
    request._body = bytes(body)
    canonical = "\n".join(
        (
            "event-interest-v1",
            timestamp,
            nonce,
            request.method,
            request.url.path,
            hashlib.sha256(body).hexdigest(),
        )
    )
    if not signature or not any(
        hmac.compare_digest(
            "v1="
            + hmac.new(s.encode(), canonical.encode(), hashlib.sha256).hexdigest(),
            signature,
        )
        for s in secrets
    ):
        raise HTTPException(401, "Invalid signature")
    try:
        await limiter.hit(f"event-interest-nonce:{nonce}", limit=1, window_seconds=600)
        await limiter.hit("event-interest-requests", limit=3000, window_seconds=60)
    except RateLimitExceeded:
        raise HTTPException(429, "Too many event response requests") from None


class InterestRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_.-]+$")
    source_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class InterestWrite(InterestRead):
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    clicks: int = Field(ge=1, le=1000)
    batch_id: str = Field(
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
    )


class InterestResponse(BaseModel):
    taps: int = Field(ge=0)
    count: int = Field(ge=0)


@router.post(
    "/read",
    response_model=InterestResponse,
    operation_id="readEventInterest",
    dependencies=[Depends(require_signed_event_interest)],
)
async def read_interest(
    body: InterestRead,
    response: Response,
    service: EventInterestService = Depends(get_event_interest_service),
):
    response.headers["Cache-Control"] = "private, no-store"
    return await service.read(body.event_id, body.source_hash)


@router.post(
    "/response",
    response_model=InterestResponse,
    operation_id="saveEventInterest",
    dependencies=[Depends(require_signed_event_interest)],
)
async def save_interest(
    body: InterestWrite,
    response: Response,
    service: EventInterestService = Depends(get_event_interest_service),
):
    response.headers["Cache-Control"] = "private, no-store"
    return await service.save(
        body.event_id, body.source_hash, body.clicks, body.batch_id
    )
