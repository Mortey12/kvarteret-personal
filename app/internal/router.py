from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import Settings
from app.dependencies import get_email_outbox_service, get_settings
from app.email_outbox_service import EmailOutboxService
from app.db.session import commit_request_session, current_session
from app.domain.recruitment_digest.service import RecruitmentDigestService
from app.warehouse_counts import refresh_counts

router = APIRouter()


@router.get("/internal/cron/refresh-warehouse-volunteer-counts", include_in_schema=False)
async def refresh_warehouse_volunteer_counts(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    if not settings.cron_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    expected = f"Bearer {settings.cron_secret}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    session = current_session()
    if session is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return {"refreshed_rows": await refresh_counts(session)}


@router.get("/internal/cron/dispatch-email-deliveries", include_in_schema=False)
async def dispatch_email_deliveries(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    service: EmailOutboxService = Depends(get_email_outbox_service),
) -> dict[str, int | bool]:
    if not settings.cron_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    expected = f"Bearer {settings.cron_secret}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not settings.email_dispatch_enabled:
        return {"disabled": True}
    summary = await service.dispatch_due(batch_size=10)
    return {
        "claimed": summary.claimed_count,
        "sent": summary.sent_count,
        "retrying": summary.retrying_count,
        "failed": summary.failed_count,
        "expired": summary.expired_count,
        "interrupted": summary.interrupted_count,
    }


@router.get("/internal/cron/enqueue-recruiter-digests", include_in_schema=False)
async def enqueue_recruiter_digests(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    service: EmailOutboxService = Depends(get_email_outbox_service),
) -> dict[str, int]:
    if not settings.cron_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    expected = f"Bearer {settings.cron_secret}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    queued = await RecruitmentDigestService(email_outbox=service).enqueue_daily_digests()
    await commit_request_session()
    return {"queued": queued}
