from __future__ import annotations

from app.observability import get_logger

import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from typing import Annotated, NoReturn
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import Settings
from app.db.rate_limit import RateLimiter, RateLimitExceeded
from app.dependencies import get_rate_limiter, get_settings

VOLUNTEER_PROSPECT_PATH = "/api/v1/volunteer-prospects"
VOLUNTEER_PROSPECT_SIGNATURE_MAX_AGE_SECONDS = 300
_VOLUNTEER_PROSPECT_NONCE_WINDOW_SECONDS = 600
VOLUNTEER_PROSPECT_IDEMPOTENCY_HEADER = "X-Kvarteret-Idempotency-Key"
VOLUNTEER_PROSPECT_CLIENT_KEY_HEADER = "X-Kvarteret-Client-Key"
_SIGNATURE_PATTERN = re.compile(r"^v(?P<version>[12])=[0-9a-f]{64}$")
_CLIENT_KEY_PATTERN = re.compile(r"^v1=[0-9a-f]{64}$")

logger = get_logger(__name__)

volunteer_prospect_signature_header = APIKeyHeader(
    name="X-Kvarteret-Signature",
    scheme_name="VolunteerProspectHmac",
    description=(
        "HMAC-SHA256 signature over the versioned request canonical form. "
        "Only the samfunnetibergen server holds the signing secret."
    ),
    auto_error=False,
)


@dataclass(frozen=True, slots=True)
class VerifiedVolunteerProspectRequest:
    body: bytes
    idempotency_key: UUID
    client_key: str | None
    signature_version: str


def build_volunteer_prospect_canonical_message(
    *,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body: bytes,
    version: str = "v1",
    idempotency_key: str | None = None,
    client_key: str | None = None,
) -> bytes:
    body_digest = hashlib.sha256(body).hexdigest()
    if version == "v1":
        parts = ("v1", timestamp, nonce, method.upper(), path, body_digest)
    elif version == "v2":
        if idempotency_key is None or client_key is None:
            raise ValueError("v2 signatures require idempotency and client keys")
        parts = (
            "v2",
            timestamp,
            nonce,
            idempotency_key,
            client_key,
            method.upper(),
            path,
            body_digest,
        )
    else:
        raise ValueError(f"Unsupported volunteer prospect signature version: {version}")
    return "\n".join(parts).encode()


def build_volunteer_prospect_signature(
    secret: str,
    *,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body: bytes,
    version: str = "v1",
    idempotency_key: str | None = None,
    client_key: str | None = None,
) -> str:
    canonical_message = build_volunteer_prospect_canonical_message(
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=path,
        body=body,
        version=version,
        idempotency_key=idempotency_key,
        client_key=client_key,
    )
    digest = hmac.new(secret.encode(), canonical_message, hashlib.sha256).hexdigest()
    return f"{version}={digest}"


async def require_signed_volunteer_prospect(
    request: Request,
    signature_header: Annotated[
        str | None,
        Security(volunteer_prospect_signature_header),
    ] = None,
    settings: Settings = Depends(get_settings),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> VerifiedVolunteerProspectRequest:
    timestamp_header = request.headers.get("X-Kvarteret-Timestamp")
    nonce_header = request.headers.get("X-Kvarteret-Nonce")
    secrets = settings.volunteer_signing_secrets
    if not secrets:
        logger.error("volunteer.prospect.auth.unconfigured", extra={"event": "volunteer.prospect.auth.unconfigured"})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable.",
        )

    if not timestamp_header or not nonce_header or not signature_header:
        _raise_authentication_failed("missing_header")
    if len(timestamp_header) > 16 or not timestamp_header.isascii():
        _raise_authentication_failed("invalid_timestamp")
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        _raise_authentication_failed("invalid_timestamp")
    if str(timestamp) != timestamp_header:
        _raise_authentication_failed("invalid_timestamp")
    if abs(int(time.time()) - timestamp) > VOLUNTEER_PROSPECT_SIGNATURE_MAX_AGE_SECONDS:
        _raise_authentication_failed("stale_timestamp")

    try:
        parsed_nonce = UUID(nonce_header)
    except ValueError:
        _raise_authentication_failed("invalid_nonce")
    if str(parsed_nonce) != nonce_header:
        _raise_authentication_failed("invalid_nonce")
    signature_match = _SIGNATURE_PATTERN.fullmatch(signature_header)
    if signature_match is None:
        _raise_authentication_failed("invalid_signature_format")

    signature_version = f"v{signature_match.group('version')}"
    idempotency_header = request.headers.get(VOLUNTEER_PROSPECT_IDEMPOTENCY_HEADER)
    client_key_header = request.headers.get(VOLUNTEER_PROSPECT_CLIENT_KEY_HEADER)
    parsed_idempotency_key: UUID | None = None
    if signature_version == "v2":
        if not idempotency_header or not client_key_header:
            _raise_authentication_failed("missing_v2_header")
        try:
            parsed_idempotency_key = UUID(idempotency_header)
        except ValueError:
            _raise_authentication_failed("invalid_idempotency_key")
        if str(parsed_idempotency_key) != idempotency_header:
            _raise_authentication_failed("invalid_idempotency_key")
        if not _CLIENT_KEY_PATTERN.fullmatch(client_key_header):
            _raise_authentication_failed("invalid_client_key")

    body = await _read_limited_body(
        request,
        max_bytes=settings.volunteer_prospect_max_body_bytes,
    )
    if signature_version == "v1":
        body_digest = hmac.new(
            settings.app_secret_key.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        parsed_idempotency_key = uuid5(
            NAMESPACE_URL,
            f"kvarteret:volunteer-prospect:legacy:{body_digest}",
        )

    assert parsed_idempotency_key is not None
    signature_matches = any(
        hmac.compare_digest(
            signature_header,
            build_volunteer_prospect_signature(
                secret,
                timestamp=timestamp_header,
                nonce=nonce_header,
                method=request.method,
                path=request.url.path,
                body=body,
                version=signature_version,
                idempotency_key=(
                    str(parsed_idempotency_key) if signature_version == "v2" else None
                ),
                client_key=(client_key_header if signature_version == "v2" else None),
            ),
        )
        for secret in secrets
    )
    if not signature_matches:
        _raise_authentication_failed("signature_mismatch")

    nonce_key = hashlib.sha256(nonce_header.encode()).hexdigest()
    try:
        await rate_limiter.hit(
            f"volunteer-prospect-nonce:{nonce_key}",
            limit=1,
            window_seconds=_VOLUNTEER_PROSPECT_NONCE_WINDOW_SECONDS,
        )
    except RateLimitExceeded:
        _raise_authentication_failed("replayed_nonce")
    except Exception as exc:
        logger.exception("volunteer.prospect.replay_check.failed", extra={"event": "volunteer.prospect.replay_check.failed"})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable.",
        ) from exc

    await _hit_rate_limit(
        rate_limiter,
        "volunteer-prospect:route",
        limit=settings.volunteer_prospect_route_limit,
        window_seconds=settings.volunteer_prospect_route_window_seconds,
    )
    if client_key_header is not None:
        await _hit_rate_limit(
            rate_limiter,
            f"volunteer-prospect:client:{client_key_header}",
            limit=settings.volunteer_prospect_client_limit,
            window_seconds=settings.volunteer_prospect_client_window_seconds,
        )

    return VerifiedVolunteerProspectRequest(
        body=body,
        idempotency_key=parsed_idempotency_key,
        client_key=client_key_header,
        signature_version=signature_version,
    )


async def _read_limited_body(request: Request, *, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            _raise_body_too_large(max_bytes)

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            _raise_body_too_large(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def _raise_body_too_large(max_bytes: int) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"Request body exceeds the {max_bytes}-byte limit.",
    )


async def _hit_rate_limit(
    rate_limiter: RateLimiter,
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    try:
        await rate_limiter.hit(
            key,
            limit=limit,
            window_seconds=window_seconds,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests.",
            headers={"Retry-After": str(window_seconds)},
        ) from exc
    except Exception as exc:
        logger.exception("volunteer.prospect.rate_limit.failed", extra={"event": "volunteer.prospect.rate_limit.failed"})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable.",
        ) from exc


def _raise_authentication_failed(reason: str) -> NoReturn:
    logger.warning(
        "Rejected volunteer prospect request authentication.",
        extra={"event": "volunteer.prospect.auth.rejected", "event_data": {"reason": reason}},
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Request authentication failed.",
        headers={"WWW-Authenticate": "HMAC"},
    )
