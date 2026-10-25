from __future__ import annotations

from app.observability import get_logger

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, Mapping
from uuid import UUID, uuid4

from email_validator import EmailNotValidError, validate_email
from opentelemetry import trace
from sqlalchemy import func, insert, select, update

from app.config import Settings
from app.db.session import commit_request_session
from app.domain.volunteer_applications.tables import (
    domain_events,
    volunteer_application_friend_invitations,
    volunteer_application_invites,
    volunteer_application_submissions,
)
from app.domain.volunteers.tables import volunteer_records
from app.email_delivery import (
    VOLUNTEER_TEMPLATE_KEYS,
    DispatchSummary,
    EmailDeliveryAttemptItem,
    EmailDeliveryDetail,
    EmailDeliveryListItem,
    EmailDeliveryRequest,
)
from app.infrastructure.email.applicant_templates import (
    ApplicantEmailTemplateRendererProtocol,
)
from app.infrastructure.email.protocols import EmailSenderProtocol
from app.infrastructure.email.resend import ResendDeliveryError
from app.db.table_defs.email_delivery import email_deliveries
from app.email_message_preparation import (
    EmailMessagePreparer,
    EmailPreparationFailure,
)
from app.email_outbox_repository import EmailOutboxRepository
from app.observability import current_trace_id

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

_LEASE_DURATION = timedelta(minutes=5)
_OPERATION_TIMEOUT_SECONDS = 20
_MAX_AUTOMATIC_ATTEMPTS = 5
_RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)


class EmailDeliveryConflictError(ValueError):
    pass


class EmailDeliveryNotFoundError(LookupError):
    pass


class RecipientCorrectionError(ValueError):
    pass


class EmailOutboxService:
    def __init__(
        self,
        *,
        settings: Settings,
        email_sender: EmailSenderProtocol,
        applicant_renderer: ApplicantEmailTemplateRendererProtocol,
        repository: EmailOutboxRepository | None = None,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.email_sender = email_sender
        self.repository = repository or EmailOutboxRepository()
        self._now = now
        self.message_preparer = EmailMessagePreparer(
            settings=settings,
            applicant_renderer=applicant_renderer,
        )

    async def enqueue(self, request: EmailDeliveryRequest) -> UUID:
        delivery_id = uuid4()
        now = self._now()
        delivery_id, created = await self.repository.enqueue(
            request,
            delivery_id=delivery_id,
            now=now,
            enqueued_trace_id=request.enqueued_trace_id or current_trace_id(),
        )
        if created:
            logger.info(
                "email.delivery",
                extra={
                    "email_delivery_id": delivery_id,
                    "registration_id": request.registration_id,
                    "template_key": request.template_key,
                    "status": "pending",
                },
                after_commit=True,
            )
        return delivery_id

    async def dispatch_due(self, *, batch_size: int = 10) -> DispatchSummary:
        if not self.settings.email_dispatch_enabled:
            return DispatchSummary(0, 0, 0, 0, 0, 0, ())
        safe_batch_size = max(1, min(batch_size, 10))
        delivery_ids, interrupted_count = await self._claim_due(safe_batch_size)
        sent = retrying = failed = expired = 0
        for delivery_id in delivery_ids:
            outcome = await self._dispatch_claimed(delivery_id)
            sent += outcome == "sent"
            retrying += outcome == "retrying"
            failed += outcome == "failed"
            expired += outcome == "expired"
        return DispatchSummary(
            claimed_count=len(delivery_ids),
            sent_count=sent,
            retrying_count=retrying,
            failed_count=failed,
            expired_count=expired,
            interrupted_count=interrupted_count,
            delivery_ids=tuple(delivery_ids),
        )

    async def _claim_due(self, batch_size: int) -> tuple[list[UUID], int]:
        now = self._now()
        claimed, interrupted_count = await self.repository.claim_due(
            batch_size=batch_size,
            now=now,
            lease_duration=_LEASE_DURATION,
        )
        await commit_request_session()
        return claimed, interrupted_count

    async def _dispatch_claimed(self, delivery_id: UUID) -> str:
        with tracer.start_as_current_span("email.delivery.dispatch") as span:
            span.set_attribute("email_delivery_id", str(delivery_id))
            return await self._dispatch_claimed_attempt(delivery_id, span)

    async def _dispatch_claimed_attempt(self, delivery_id: UUID, span) -> str:
        attempt_id, attempt_no, registration_id = await self._start_attempt(delivery_id)
        span.set_attribute("attempt_no", attempt_no)
        if registration_id is not None:
            span.set_attribute("registration_id", registration_id)
        started = perf_counter()
        stage = "prepare"
        try:
            async with asyncio.timeout(_OPERATION_TIMEOUT_SECONDS):
                prepared = await self._prepare_email(delivery_id)
                stage = "render"
                await self._set_attempt_stage(attempt_id, stage)
                # Rendering happens as part of preparation. The explicit stage
                # remains useful if the process disappears before Resend accepts the message.
                stage = "smtp"
                await self._set_attempt_stage(attempt_id, stage)
                await self.email_sender.send_email(
                    recipient_email=prepared.recipient_email,
                    subject=prepared.subject,
                    html_body=prepared.html_body,
                    idempotency_key=f"email-delivery/{delivery_id}",
                )
        except EmailPreparationFailure as exc:
            return await self._finish_failure(
                delivery_id,
                attempt_id,
                attempt_no,
                stage=stage,
                category=exc.category,
                registration_id=registration_id,
                retryable=exc.retryable,
                expired=exc.expired,
                duration_ms=_duration_ms(started),
            )
        except ResendDeliveryError as exc:
            return await self._finish_failure(
                delivery_id,
                attempt_id,
                attempt_no,
                stage=stage,
                category=exc.category,
                registration_id=registration_id,
                retryable=exc.retryable,
                smtp_status=None,
                duration_ms=_duration_ms(started),
            )
        except TimeoutError:
            return await self._finish_failure(
                delivery_id,
                attempt_id,
                attempt_no,
                stage=stage,
                category="timeout",
                registration_id=registration_id,
                retryable=True,
                duration_ms=_duration_ms(started),
            )
        except (ConnectionError, OSError):
            return await self._finish_failure(
                delivery_id,
                attempt_id,
                attempt_no,
                stage=stage,
                category="connection",
                registration_id=registration_id,
                retryable=True,
                duration_ms=_duration_ms(started),
            )
        except Exception:
            logger.exception(
                "email.delivery.unexpected",
                extra={
                    "event": "email.delivery",
                    "event_data": {
                        "email_delivery_id": delivery_id,
                        "failure_stage": stage,
                        "error_category": "unexpected",
                    },
                },
            )
            return await self._finish_failure(
                delivery_id,
                attempt_id,
                attempt_no,
                stage=stage,
                category="unexpected",
                registration_id=registration_id,
                retryable=False,
                duration_ms=_duration_ms(started),
            )

        now = self._now()
        duration_ms = _duration_ms(started)
        await self.repository.finish_success(
            delivery_id,
            attempt_id,
            now=now,
            duration_ms=duration_ms,
        )
        await commit_request_session()
        logger.info(
            "email.delivery",
            extra={
                "email_delivery_id": delivery_id,
                "registration_id": registration_id,
                "status": "sent",
                "outcome": "succeeded",
                "attempt_no": attempt_no,
                "duration_ms": duration_ms,
            },
        )
        return "sent"

    async def _start_attempt(self, delivery_id: UUID) -> tuple[int, int, int | None]:
        result = await self.repository.start_attempt(delivery_id, now=self._now())
        await commit_request_session()
        return result

    async def _set_attempt_stage(self, attempt_id: int, stage: str) -> None:
        await self.repository.set_attempt_stage(attempt_id, stage)
        await commit_request_session()

    async def _finish_failure(
        self,
        delivery_id: UUID,
        attempt_id: int,
        attempt_no: int,
        *,
        stage: str,
        category: str,
        registration_id: int | None,
        retryable: bool,
        duration_ms: int,
        smtp_status: int | None = None,
        expired: bool = False,
    ) -> str:
        now = self._now()
        should_retry = retryable and attempt_no < _MAX_AUTOMATIC_ATTEMPTS
        status = "pending" if should_retry else ("expired" if expired else "failed")
        outcome = "retryable_failure" if should_retry else "permanent_failure"
        values: dict[str, Any] = {
            "status": status,
            "last_error_category": category,
            "updated_at": now,
            "lease_owner": None,
            "lease_until": None,
        }
        if should_retry:
            values["next_attempt_at"] = now + _RETRY_DELAYS[attempt_no - 1]
        elif status != "failed":
            # A failed delivery keeps its context so an admin retry can reuse it.
            values["encrypted_context"] = None
        await self.repository.finish_failure(
            delivery_id,
            attempt_id,
            stage=stage,
            outcome=outcome,
            category=category,
            smtp_status=smtp_status,
            duration_ms=duration_ms,
            delivery_values=values,
            now=now,
        )
        await commit_request_session()
        logger.log(
            logging.WARNING if should_retry else logging.ERROR,
            "email.delivery",
            extra={
                "email_delivery_id": delivery_id,
                "registration_id": registration_id,
                "status": status,
                "outcome": outcome,
                "failure_stage": stage,
                "error_category": category,
                "smtp_status_class": smtp_status // 100 if smtp_status else None,
                "attempt_no": attempt_no,
                "duration_ms": duration_ms,
            },
        )
        return "retrying" if should_retry else status

    async def _prepare_email(self, delivery_id: UUID):
        row = await self.repository.get_delivery_row(delivery_id)
        first_attempt = await self.repository.first_attempt_at(delivery_id)
        if first_attempt is not None:
            if first_attempt.tzinfo is None:
                first_attempt = first_attempt.replace(tzinfo=UTC)
            if self._now() - first_attempt >= timedelta(hours=23):
                raise EmailPreparationFailure(
                    "idempotency_window_expired", expired=True
                )
        return await self.message_preparer.prepare(row)

    async def list_deliveries(
        self,
        *,
        status: str | None = "failed",
        template_key: str | None = None,
        created_after: datetime | None = None,
        limit: int = 100,
    ) -> list[EmailDeliveryListItem]:
        rows = await self.repository.list_delivery_rows(
            status=status,
            template_key=template_key,
            created_after=created_after,
            limit=limit,
        )
        return [_list_item(row) for row in rows]

    async def get_delivery(self, delivery_id: UUID) -> EmailDeliveryDetail | None:
        delivery = await self.repository.get_delivery_row_or_none(delivery_id)
        if delivery is None:
            return None
        attempts = await self.repository.get_attempt_rows(delivery_id)
        return EmailDeliveryDetail(
            delivery=_list_item(delivery),
            attempts=tuple(_attempt_item(row) for row in attempts),
        )

    async def get_latest_for_registration(
        self, registration_id: int
    ) -> EmailDeliveryListItem | None:
        row = await self.repository.get_latest_registration_row(registration_id)
        return _list_item(row) if row is not None else None

    async def retry_failed(
        self, delivery_id: UUID, *, actor_user_account_id: int
    ) -> UUID:
        row = await self._load_retryable_row(delivery_id)
        successor = await self._create_successor(
            row, recipient_email=row["recipient_email"]
        )
        await self._record_admin_recovery_event(
            row,
            actor_user_account_id=actor_user_account_id,
            action="email_delivery_retried",
            successor_delivery_id=successor,
        )
        return successor

    async def correct_volunteer_recipient(
        self,
        delivery_id: UUID,
        *,
        recipient_email: str,
        actor_user_account_id: int,
    ) -> UUID:
        row = await self._load_retryable_row(delivery_id)
        if (
            row["template_key"] not in VOLUNTEER_TEMPLATE_KEYS
            or row["registration_id"] is None
        ):
            raise RecipientCorrectionError(
                "Recipient correction is only available for volunteer application email."
            )
        try:
            normalized = validate_email(
                recipient_email.strip(), check_deliverability=False
            ).normalized.lower()
        except EmailNotValidError as exc:
            raise RecipientCorrectionError("Enter a valid email address.") from exc
        session = _session()
        registration_id = int(row["registration_id"])
        invite = (
            (
                await session.execute(
                    select(volunteer_application_invites.c.promoted_volunteer_id).where(
                        volunteer_application_invites.c.id == registration_id
                    )
                )
            )
            .mappings()
            .first()
        )
        if invite is None:
            raise RecipientCorrectionError("Volunteer application no longer exists.")
        duplicate_registration = await session.scalar(
            select(volunteer_application_invites.c.id).where(
                volunteer_application_invites.c.id != registration_id,
                func.lower(volunteer_application_invites.c.email) == normalized,
            )
        )
        promoted_id = invite["promoted_volunteer_id"]
        duplicate_volunteer = await session.scalar(
            select(volunteer_records.c.id).where(
                func.lower(func.coalesce(volunteer_records.c.email, "")) == normalized,
                *(
                    [volunteer_records.c.id != promoted_id]
                    if promoted_id is not None
                    else []
                ),
            )
        )
        if duplicate_registration is not None or duplicate_volunteer is not None:
            raise RecipientCorrectionError("That email address is already in use.")
        await session.execute(
            update(volunteer_application_invites)
            .where(volunteer_application_invites.c.id == registration_id)
            .values(email=normalized)
        )
        await session.execute(
            update(volunteer_application_submissions)
            .where(volunteer_application_submissions.c.invite_id == registration_id)
            .values(email=normalized)
        )
        await session.execute(
            update(volunteer_application_friend_invitations)
            .where(
                volunteer_application_friend_invitations.c.invitee_application_id
                == registration_id
            )
            .values(invitee_email_snapshot=normalized)
        )
        await session.execute(
            update(volunteer_application_friend_invitations)
            .where(
                volunteer_application_friend_invitations.c.inviter_application_id
                == registration_id
            )
            .values(inviter_email_snapshot=normalized)
        )
        if promoted_id is not None:
            await session.execute(
                update(volunteer_records)
                .where(volunteer_records.c.id == promoted_id)
                .values(email=normalized)
            )
        successor = await self._create_successor(row, recipient_email=normalized)
        await self._record_admin_recovery_event(
            row,
            actor_user_account_id=actor_user_account_id,
            action="email_recipient_corrected",
            successor_delivery_id=successor,
        )
        return successor

    async def _load_retryable_row(self, delivery_id: UUID) -> Mapping[str, Any]:
        row = await self.repository.get_delivery_row_or_none(
            delivery_id, for_update=True
        )
        if row is None:
            raise EmailDeliveryNotFoundError("Email delivery was not found.")
        if row["status"] != "failed" and not (
            row["status"] == "expired"
            and row["last_error_category"] == "idempotency_window_expired"
        ):
            raise EmailDeliveryConflictError("Only failed deliveries can be retried.")
        return row

    async def _create_successor(
        self, row: Mapping[str, Any], *, recipient_email: str
    ) -> UUID:
        return await self.enqueue(
            EmailDeliveryRequest(
                template_key=row["template_key"],
                template_version=row["template_version"],
                recipient_email=recipient_email,
                business_type=row["business_type"],
                business_id=row["business_id"],
                idempotency_key=f"manual:{row['id']}:{uuid4().hex}",
                registration_id=row["registration_id"],
                source_domain_event_id=row["source_domain_event_id"],
                enqueued_trace_id=current_trace_id(),
                supersedes_delivery_id=row["id"],
                encrypted_context=row["encrypted_context"],
            )
        )

    async def _record_admin_recovery_event(
        self,
        row: Mapping[str, Any],
        *,
        actor_user_account_id: int,
        action: str,
        successor_delivery_id: UUID | None = None,
    ) -> None:
        successor_delivery_id = successor_delivery_id or await _session().scalar(
            select(email_deliveries.c.id)
            .where(email_deliveries.c.supersedes_delivery_id == row["id"])
            .order_by(email_deliveries.c.created_at.desc())
            .limit(1)
        )
        if row["registration_id"] is not None:
            await _session().execute(
                insert(domain_events).values(
                    event_type=action,
                    actor_user_account_id=actor_user_account_id,
                    subject_type="application",
                    subject_id=row["registration_id"],
                    payload={
                        "delivery_id": str(row["id"]),
                        "successor_delivery_id": (
                            str(successor_delivery_id)
                            if successor_delivery_id
                            else None
                        ),
                    },
                    occurred_at=self._now(),
                    trace_id=current_trace_id(),
                )
            )


def _session():
    from app.db.session import current_session

    session = current_session()
    if session is None:
        raise RuntimeError("Email delivery requires an active database session.")
    return session


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _mask_email(value: str) -> str:
    local, separator, domain = value.partition("@")
    if not separator:
        return "***"
    visible = local[:1]
    return f"{visible}{'*' * max(2, len(local) - 1)}@{domain}"


def _list_item(row: Mapping[str, Any]) -> EmailDeliveryListItem:
    return EmailDeliveryListItem(
        delivery_id=row["id"],
        template_key=row["template_key"],
        masked_recipient=_mask_email(row["recipient_email"]),
        business_type=row["business_type"],
        business_id=row["business_id"],
        registration_id=row["registration_id"],
        status=row["status"],
        automatic_attempt_count=row["automatic_attempt_count"],
        last_error_category=row["last_error_category"],
        next_attempt_at=row["next_attempt_at"],
        enqueued_trace_id=row["enqueued_trace_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        sent_at=row["sent_at"],
        supersedes_delivery_id=row["supersedes_delivery_id"],
    )


def _attempt_item(row: Mapping[str, Any]) -> EmailDeliveryAttemptItem:
    return EmailDeliveryAttemptItem(
        attempt_no=row["attempt_no"],
        stage=row["stage"],
        outcome=row["outcome"],
        error_category=row["error_category"],
        smtp_status=row["smtp_status"],
        smtp_status_class=row["smtp_status_class"],
        duration_ms=row["duration_ms"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )
