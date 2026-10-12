import json
import time
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.booking_request_auth import booking_signature
from app.api.v1.booking_requests import router
from app.config import Settings
from app.db.rate_limit import InMemoryRateLimiter
from app.dependencies import get_settings, get_rate_limiter
from app.domain.booking_requests.models import BookingSnapshot, BookingReceipt
from app.domain.booking_requests.service import BookingRequestsService

SECRET = "0123456789abcdef0123456789abcdef"


def snapshot():
    return {
        "schema_version": 1,
        "submission_id": str(uuid4()),
        "kind": "room",
        "event_name": "Konsert",
        "contact_name": "Kari",
        "contact_email": "kari@example.com",
        "room_ids": [97],
        "schedule": [
            {"date": "2026-10-15", "doors_open": "20:00", "doors_close": "02:00"}
        ],
        "form": {"description": "Test"},
        "crescat_payload": {"name": "Konsert"},
    }


def headers(body, submission_id, timestamp=None, nonce=None, secret=SECRET):
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or str(uuid4())
    return {
        "Content-Type": "application/json",
        "X-Kvarteret-Timestamp": timestamp,
        "X-Kvarteret-Nonce": nonce,
        "X-Kvarteret-Idempotency-Key": submission_id,
        "X-Kvarteret-Signature": booking_signature(
            secret, timestamp, nonce, submission_id, body
        ),
    }


def app(**settings):
    instance = FastAPI()
    instance.include_router(router, prefix="/api/v1/booking-requests")
    limiter = InMemoryRateLimiter()
    settings = settings or {"volunteer_prospect_hmac_secret": SECRET}
    instance.dependency_overrides[get_settings] = lambda: Settings(
        app_env="test", **settings
    )
    instance.dependency_overrides[get_rate_limiter] = lambda: limiter
    return instance


def test_acknowledges_only_after_commit(monkeypatch):
    data = snapshot()
    body = json.dumps(data).encode()
    order = []

    async def store(self, snapshot):
        order.append("store")
        return BookingReceipt(
            booking_request_id=uuid4(),
            submission_id=snapshot.submission_id,
            content_hash="a" * 64,
        )

    async def commit():
        order.append("commit")

    monkeypatch.setattr(BookingRequestsService, "store", store)
    monkeypatch.setattr("app.api.v1.booking_requests.commit_request_session", commit)
    response = TestClient(app()).post(
        "/api/v1/booking-requests",
        content=body,
        headers=headers(body, data["submission_id"]),
    )
    assert response.status_code == 201
    assert order == ["store", "commit"]


def test_commit_failure_never_returns_receipt(monkeypatch):
    data = snapshot()
    body = json.dumps(data).encode()
    monkeypatch.setattr(
        BookingRequestsService,
        "store",
        AsyncMock(
            return_value=BookingReceipt(
                booking_request_id=uuid4(),
                submission_id=data["submission_id"],
                content_hash="a" * 64,
            )
        ),
    )
    monkeypatch.setattr(
        "app.api.v1.booking_requests.commit_request_session",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(
        "app.api.v1.booking_requests.rollback_request_session", AsyncMock()
    )
    response = TestClient(app()).post(
        "/api/v1/booking-requests",
        content=body,
        headers=headers(body, data["submission_id"]),
    )
    assert response.status_code == 503
    assert "booking_request_id" not in response.json()


def test_rejects_tampering_stale_signatures_and_missing_doors():
    client = TestClient(app())
    data = snapshot()
    body = json.dumps(data).encode()
    assert (
        client.post(
            "/api/v1/booking-requests",
            content=body + b" ",
            headers=headers(body, data["submission_id"]),
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/booking-requests",
            content=body,
            headers=headers(body, data["submission_id"], timestamp="1"),
        ).status_code
        == 401
    )
    data["schedule"][0]["doors_close"] = ""
    body = json.dumps(data).encode()
    response = client.post(
        "/api/v1/booking-requests",
        content=body,
        headers=headers(body, data["submission_id"]),
    )
    assert response.status_code == 422
    assert "kari@example.com" not in response.text


def test_limits_body_and_rejects_replayed_nonce(monkeypatch):
    data = snapshot()
    body = json.dumps(data).encode()
    client = TestClient(app())
    monkeypatch.setattr(
        BookingRequestsService,
        "store",
        AsyncMock(
            return_value=BookingReceipt(
                booking_request_id=uuid4(),
                submission_id=data["submission_id"],
                content_hash="a" * 64,
            )
        ),
    )
    monkeypatch.setattr(
        "app.api.v1.booking_requests.commit_request_session", AsyncMock()
    )
    auth = headers(body, data["submission_id"])
    assert (
        client.post("/api/v1/booking-requests", content=body, headers=auth).status_code
        == 201
    )
    assert (
        client.post("/api/v1/booking-requests", content=body, headers=auth).status_code
        == 429
    )
    huge = b"a" * (256 * 1024 + 1)
    assert (
        client.post(
            "/api/v1/booking-requests",
            content=huge,
            headers=headers(huge, data["submission_id"]),
        ).status_code
        == 413
    )


@pytest.mark.asyncio
async def test_normalizes_snapshot_hash_for_retries_and_keeps_edited_submissions():
    repo = AsyncMock()
    repo.store.return_value = uuid4()
    service = BookingRequestsService(repo)
    data = snapshot()
    one = await service.store(BookingSnapshot.model_validate(data))
    two = await service.store(
        BookingSnapshot.model_validate(dict(reversed(list(data.items()))))
    )
    assert one.content_hash == two.content_hash
    data["event_name"] = "Edited"
    three = await service.store(BookingSnapshot.model_validate(data))
    assert three.submission_id == one.submission_id
    assert three.content_hash != one.content_hash


def test_openapi_schedule_schema_has_no_unresolved_local_reference():
    schema = app().openapi()["paths"]["/api/v1/booking-requests"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    schedule = schema["properties"]["schedule"]["items"]
    assert schedule["required"] == ["date", "doors_open", "doors_close"]
    assert "$ref" not in schedule


def prefill_headers(body, submission_id):
    auth = headers(body, submission_id)
    auth["X-Kvarteret-Signature"] = booking_signature(
        SECRET,
        auth["X-Kvarteret-Timestamp"],
        auth["X-Kvarteret-Nonce"],
        submission_id,
        body,
        "/api/v1/booking-requests/prefill",
    )
    return auth


def test_prefill_requires_path_bound_auth_and_omits_private_fields(monkeypatch):
    from app.domain.booking_requests.repository import BookingRequestsRepository

    data = snapshot()
    data["form"]["invoiceAddress"] = "Private address"
    receipt_id = str(uuid4())
    lookup = json.dumps({"booking_request_id": receipt_id}).encode()
    repo = AsyncMock(return_value=data)
    monkeypatch.setattr(BookingRequestsRepository, "get_snapshot", repo)
    client = TestClient(app())
    assert (
        client.post("/api/v1/booking-requests/prefill", content=lookup).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/booking-requests/prefill",
            content=lookup,
            headers=headers(lookup, data["submission_id"]),
        ).status_code
        == 401
    )
    response = client.post(
        "/api/v1/booking-requests/prefill",
        content=lookup,
        headers=prefill_headers(lookup, data["submission_id"]),
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Test"
    assert "Private address" not in response.text
    assert "crescat_payload" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert str(repo.call_args.args[0]) == receipt_id
    assert str(repo.call_args.args[1]) == data["submission_id"]


def test_prefill_missing_or_karaoke_snapshot_returns_not_found(monkeypatch):
    from app.domain.booking_requests.repository import BookingRequestsRepository

    data = snapshot()
    lookup = json.dumps({"booking_request_id": str(uuid4())}).encode()
    client = TestClient(app())
    for stored in (None, dict(data, kind="karaoke")):
        monkeypatch.setattr(
            BookingRequestsRepository, "get_snapshot", AsyncMock(return_value=stored)
        )
        assert (
            client.post(
                "/api/v1/booking-requests/prefill",
                content=lookup,
                headers=prefill_headers(lookup, data["submission_id"]),
            ).status_code
            == 404
        )


def test_shared_signing_secret_and_legacy_secret_are_accepted_during_cutover(
    monkeypatch,
):
    shared = "personal-signing-test-secret-0123456789"
    event_interest_secret = "event-interest-test-secret-0123456789"

    async def store(self, snapshot):
        return BookingReceipt(
            booking_request_id=uuid4(),
            submission_id=snapshot.submission_id,
            content_hash="a" * 64,
        )

    monkeypatch.setattr(BookingRequestsService, "store", store)
    monkeypatch.setattr(
        "app.api.v1.booking_requests.commit_request_session", AsyncMock()
    )
    client = TestClient(
        app(
            personal_signing_secret=shared,
            volunteer_prospect_hmac_secret=SECRET,
            event_interest_secret=event_interest_secret,
        )
    )
    for secret, status in (
        (shared, 201),
        (SECRET, 201),
        (event_interest_secret, 401),
    ):
        data = snapshot()
        body = json.dumps(data).encode()
        response = client.post(
            "/api/v1/booking-requests",
            content=body,
            headers=headers(body, data["submission_id"], secret=secret),
        )
        assert response.status_code == status
