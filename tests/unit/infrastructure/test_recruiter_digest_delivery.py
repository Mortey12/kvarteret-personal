from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import reset_request_session, set_request_session
from app.db.table_defs.email_delivery import email_deliveries, email_delivery_attempts
from app.db.tables import public_metadata
from app.domain.groups.tables import groups
from app.domain.recruitment_digest.service import RecruitmentDigestService
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.volunteer_applications.tables import (
    domain_events,
    volunteer_application_invites,
    volunteer_application_submissions,
)
from app.domain.volunteers.tables import volunteer_records
from app.email_delivery import RECRUITER_DAILY_DIGEST, EmailDeliveryRequest
from app.infrastructure.email.resend import ResendDeliveryError
from tests.unit.infrastructure.test_email_delivery_service import (
    Clock,
    FakeApplicantRenderer,
    FakeSender,
    Rendered,
    _service,
)


class DigestRenderer(FakeApplicantRenderer):
    def __init__(self) -> None:
        super().__init__()
        self.digests: list[dict[str, str]] = []

    def render_recruiter_digest_email(self, **kwargs):
        self.digests.append(kwargs)
        return Rendered()


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"public": None}},
    )
    tables = [
        volunteer_records,
        groups,
        assignment_roles,
        role_assignments,
        volunteer_application_invites,
        volunteer_application_submissions,
        domain_events,
        email_deliveries,
        email_delivery_attempts,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: public_metadata.create_all(
                sync_connection, tables=tables
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        token = set_request_session(value)
        try:
            yield value
        finally:
            reset_request_session(token)
    await engine.dispose()


def _digest_service(sender: FakeSender, clock: Clock):
    service = _service(sender, clock)
    renderer = DigestRenderer()
    service.message_preparer.applicant_renderer = renderer
    return service, renderer


async def _seed_new_applicant(session, clock: Clock) -> None:
    await session.execute(
        insert(groups).values(
            id=1,
            slug="bar",
            name="Bar",
            is_active=True,
            active_through_semester=20262,
            recruitment_contact_email="rekruttering@example.test",
        )
    )
    await session.execute(
        insert(volunteer_application_invites).values(
            id=7,
            token=str(uuid4()),
            email="applicant@example.test",
            source="public_signup",
            status="new",
            trial_shift_attended=False,
            first_choice_group_id=1,
            created_at=clock.value - timedelta(hours=3),
        )
    )
    await session.execute(
        insert(volunteer_application_submissions).values(
            id=1,
            invite_id=7,
            first_name="Synthetic",
            last_name="Applicant",
            email="applicant@example.test",
            gender="A",
            created_at=clock.value - timedelta(hours=3),
        )
    )


async def _enqueue_raw_digest(service, context: bytes):
    return await service.enqueue(
        EmailDeliveryRequest(
            template_key=RECRUITER_DAILY_DIGEST,
            template_version=1,
            recipient_email="rekruttering@example.test",
            business_type="recruitment_digest",
            business_id="2026-08-11",
            idempotency_key=f"recruiter-digest:{uuid4().hex}",
            encrypted_context=context,
        )
    )


async def _delivery(session, delivery_id):
    return (
        (
            await session.execute(
                select(email_deliveries).where(email_deliveries.c.id == delivery_id)
            )
        )
        .mappings()
        .one()
    )


@pytest.mark.asyncio
async def test_daily_digest_round_trip_reaches_sent(session) -> None:
    clock = Clock()
    await _seed_new_applicant(session, clock)
    sender = FakeSender()
    service, renderer = _digest_service(sender, clock)

    queued = await RecruitmentDigestService(email_outbox=service).enqueue_daily_digests(now=clock.value)
    await session.commit()
    summary = await service.dispatch_due()

    assert queued == 1
    assert summary.sent_count == 1
    assert sender.sent == 1
    assert renderer.digests[0]["heading"] == "Rekruttering for Bar"
    assert "Synthetic Applicant" in renderer.digests[0]["sections_html"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context",
    [
        b"not json",
        b"[7]",
        json.dumps({"new": 7}).encode(),
        json.dumps({"new": [7], "_group_id": "1"}).encode(),
        json.dumps({"new": [7], "unknown_section": [8]}).encode(),
        json.dumps({"_missing_groups": [{"group_id": 1}]}).encode(),
    ],
)
async def test_unexpected_digest_context_fails_with_clear_category(
    session, context
) -> None:
    clock = Clock()
    sender = FakeSender()
    service, _renderer = _digest_service(sender, clock)
    delivery_id = await _enqueue_raw_digest(service, context)
    await session.commit()

    summary = await service.dispatch_due()

    assert summary.failed_count == 1
    assert sender.sent == 0
    delivery = await _delivery(session, delivery_id)
    assert delivery["last_error_category"] == "digest_context_invalid"


@pytest.mark.asyncio
async def test_admin_retry_of_failed_digest_reuses_its_context(
    session, monkeypatch
) -> None:
    clock = Clock()
    await _seed_new_applicant(session, clock)
    sender = FakeSender(
        [ResendDeliveryError("resend_permanent", retryable=False, http_status=422)]
    )
    service, renderer = _digest_service(sender, clock)
    context = json.dumps({"new": [7], "_group_id": 1, "_name": None}).encode()
    delivery_id = await _enqueue_raw_digest(service, context)
    await session.commit()
    assert (await service.dispatch_due()).failed_count == 1

    async def skip_audit(*args, **kwargs):
        pass

    monkeypatch.setattr(service, "_record_admin_recovery_event", skip_audit)
    successor_id = await service.retry_failed(delivery_id, actor_user_account_id=1)
    await session.commit()

    assert (await _delivery(session, successor_id))["encrypted_context"] == context
    assert (await service.dispatch_due()).sent_count == 1
    assert renderer.digests[-1]["heading"] == "Rekruttering for Bar"
