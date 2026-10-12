from __future__ import annotations

import hashlib
import hmac
import time
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.config import Settings
from app.db.rate_limit import RateLimiter, RateLimitExceeded
from app.dependencies import get_rate_limiter, get_settings

MAX_BODY_BYTES = 256 * 1024
signature_header = APIKeyHeader(
    name="X-Kvarteret-Signature", scheme_name="BookingRequestHmac", auto_error=False
)


def booking_signature(
    secret: str,
    timestamp: str,
    nonce: str,
    submission_id: str,
    body: bytes,
    path: str = "/api/v1/booking-requests",
) -> str:
    message = "\n".join(
        (
            "booking-v1",
            timestamp,
            nonce,
            submission_id,
            "POST",
            path,
            hashlib.sha256(body).hexdigest(),
        )
    )
    return (
        "v1=" + hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    )


async def require_signed_booking(
    request: Request,
    signature: Annotated[str | None, Security(signature_header)] = None,
    settings: Settings = Depends(get_settings),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> tuple[bytes, UUID]:
    secrets = [s for s in settings.volunteer_signing_secrets if len(s) >= 32]
    if not secrets:
        raise HTTPException(503, "Booking storage unavailable.")
    timestamp = request.headers.get("X-Kvarteret-Timestamp", "")
    nonce = request.headers.get("X-Kvarteret-Nonce", "")
    submission_id = request.headers.get("X-Kvarteret-Idempotency-Key", "")
    try:
        if (
            len(timestamp) > 16
            or str(int(timestamp)) != timestamp
            or abs(int(time.time()) - int(timestamp)) > 300
        ):
            raise ValueError
        if str(UUID(nonce)) != nonce or str(UUID(submission_id)) != submission_id:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(401, "Invalid booking authentication.") from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "Booking snapshot too large.")
    raw = bytes(body)
    if not signature or not any(
        hmac.compare_digest(
            signature,
            booking_signature(
                s, timestamp, nonce, submission_id, raw, request.url.path
            ),
        )
        for s in secrets
    ):
        raise HTTPException(401, "Invalid booking authentication.")
    try:
        await rate_limiter.hit(
            "booking-nonce:" + hashlib.sha256(nonce.encode()).hexdigest(),
            limit=1,
            window_seconds=600,
        )
        await rate_limiter.hit("booking-storage:route", limit=120, window_seconds=60)
    except RateLimitExceeded:
        raise HTTPException(429, "Too many requests.") from None
    except Exception:
        raise HTTPException(503, "Booking storage unavailable.") from None
    return raw, UUID(submission_id)
