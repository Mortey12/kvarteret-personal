from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


APPLICANT_INVITATION = "applicant_invitation"
APPLICANT_FRIEND_INVITATION = "applicant_friend_invitation"
APPLICANT_PROFILE_COMPLETION = "applicant_profile_completion"
APPLICANT_APPLICATION_RECEIVED = "applicant_application_received"
RECRUITER_DAILY_DIGEST = "recruiter_daily_digest"

VOLUNTEER_TEMPLATE_KEYS = frozenset(
    {
        APPLICANT_INVITATION,
        APPLICANT_FRIEND_INVITATION,
        APPLICANT_PROFILE_COMPLETION,
        APPLICANT_APPLICATION_RECEIVED,
    }
)


@dataclass(frozen=True, slots=True)
class EmailDeliveryRequest:
    template_key: str
    template_version: int
    recipient_email: str
    business_type: str
    business_id: str
    idempotency_key: str
    registration_id: int | None = None
    source_domain_event_id: int | None = None
    encrypted_context: bytes | None = None
    enqueued_trace_id: str | None = None
    supersedes_delivery_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DispatchSummary:
    claimed_count: int
    sent_count: int
    retrying_count: int
    failed_count: int
    expired_count: int
    interrupted_count: int
    delivery_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class EmailDeliveryListItem:
    delivery_id: UUID
    template_key: str
    masked_recipient: str
    business_type: str
    business_id: str
    registration_id: int | None
    status: str
    automatic_attempt_count: int
    last_error_category: str | None
    next_attempt_at: datetime | None
    enqueued_trace_id: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None
    supersedes_delivery_id: UUID | None


@dataclass(frozen=True, slots=True)
class EmailDeliveryAttemptItem:
    attempt_no: int
    stage: str
    outcome: str
    error_category: str | None
    smtp_status: int | None
    smtp_status_class: int | None
    duration_ms: int | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class EmailDeliveryDetail:
    delivery: EmailDeliveryListItem
    attempts: tuple[EmailDeliveryAttemptItem, ...]


class EmailDeliveryOutboxProtocol(Protocol):
    async def enqueue(self, request: EmailDeliveryRequest) -> UUID: ...

    async def dispatch_due(self, *, batch_size: int = 10) -> DispatchSummary: ...
