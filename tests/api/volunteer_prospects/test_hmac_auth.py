from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.request_auth import (
    VOLUNTEER_PROSPECT_PATH,
    build_volunteer_prospect_signature,
)
from app.api.v1.volunteer_prospects import router
from app.config import Settings
from app.db.rate_limit import InMemoryRateLimiter
from app.dependencies import (
    get_rate_limiter,
    get_settings,
    get_volunteer_applications_service,
)
from app.domain.volunteer_applications.models import VolunteerProspectIdempotencyConflictError
from app.domain.volunteer_applications.service import (
    ActiveVolunteerRegistrationExistsError,
    VolunteerAlreadyExistsError,
    VolunteerApplicationFieldConflictError,
)

HMAC_SECRET = "test-shared-secret-0123456789abcdef"
PREVIOUS_HMAC_SECRET = "previous-shared-secret-0123456789abc"
FIXED_TIMESTAMP = "1760000000"
FIXED_NONCE = "123e4567-e89b-42d3-a456-426614174000"
FIXED_IDEMPOTENCY_KEY = "123e4567-e89b-42d3-a456-426614174001"
FIXED_CLIENT_KEY = (
    "v1=9a0dcfcc46f5fc85d1151d64c99635e5d32febbe873c1d9467cf618652eb84e5"
)
FIXED_BODY = (
    b'{"full_name":"Kari Nordmann","email":"kari@example.com"}'
)
FIXED_SIGNATURE = (
    "v1=3cfde76a38294df695c172ea02132ea8902860cd6709894e1d93779ccbec6b53"
)

VALID_PAYLOAD = {
    "full_name": "Kari Nordmann",
    "email": "kari@example.com",
    "phone": "+4740612345",
    "study_institution": "UiB",
    "first_choice_group_slug": "kraftetaten",
}


class FakeVolunteerApplicationsService:
    def __init__(self) -> None:
        self.calls = 0
        self.idempotency_keys = []
        self.request_hashes = []
        self.error: Exception | None = None

    async def create_public_prospect_registration(
        self,
        registration,
        *,
        base_url,
        idempotency_key=None,
        request_hash=None,
    ):
        self.calls += 1
        self.idempotency_keys.append(idempotency_key)
        self.request_hashes.append(request_hash)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(registration_id=42, registration_ids=(42,))


def _build_app(
    *,
    secret: str | None = HMAC_SECRET,
    previous_secret: str | None = None,
    **setting_overrides,
) -> tuple[FastAPI, FakeVolunteerApplicationsService]:
    app = FastAPI()
    app.include_router(router, prefix=VOLUNTEER_PROSPECT_PATH)
    service = FakeVolunteerApplicationsService()
    limiter = InMemoryRateLimiter()
    settings = {
        "personal_signing_secret": secret,
        "personal_signing_previous_secret": previous_secret,
        **setting_overrides,
    }
    app.dependency_overrides[get_settings] = lambda: Settings(app_env="test", **settings)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_volunteer_applications_service] = lambda: service
    return app, service


def _encoded_payload(payload: dict | None = None) -> bytes:
    return json.dumps(payload or VALID_PAYLOAD, separators=(",", ":")).encode()


def _signed_headers(
    body: bytes,
    *,
    secret: str = HMAC_SECRET,
    timestamp: str | None = None,
    nonce: str | None = None,
    idempotency_key: str | None = None,
    client_key: str = FIXED_CLIENT_KEY,
    version: str = "v2",
) -> dict[str, str]:
    resolved_timestamp = timestamp or str(int(time.time()))
    resolved_nonce = nonce or str(uuid4())
    resolved_idempotency_key = idempotency_key or str(uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Kvarteret-Timestamp": resolved_timestamp,
        "X-Kvarteret-Nonce": resolved_nonce,
        "X-Kvarteret-Signature": build_volunteer_prospect_signature(
            secret,
            timestamp=resolved_timestamp,
            nonce=resolved_nonce,
            method="POST",
            path=VOLUNTEER_PROSPECT_PATH,
            body=body,
            version=version,
            idempotency_key=(resolved_idempotency_key if version == "v2" else None),
            client_key=(client_key if version == "v2" else None),
        ),
    }
    if version == "v2":
        headers["X-Kvarteret-Idempotency-Key"] = resolved_idempotency_key
        headers["X-Kvarteret-Client-Key"] = client_key
    return headers


def test_hmac_signature_matches_shared_cross_repository_vector() -> None:
    assert (
        build_volunteer_prospect_signature(
            HMAC_SECRET,
            timestamp=FIXED_TIMESTAMP,
            nonce=FIXED_NONCE,
            method="POST",
            path=VOLUNTEER_PROSPECT_PATH,
            body=FIXED_BODY,
        )
        == FIXED_SIGNATURE
    )


def test_v2_hmac_signature_matches_shared_cross_repository_vector() -> None:
    assert build_volunteer_prospect_signature(
        HMAC_SECRET,
        timestamp=FIXED_TIMESTAMP,
        nonce=FIXED_NONCE,
        method="POST",
        path=VOLUNTEER_PROSPECT_PATH,
        body=FIXED_BODY,
        version="v2",
        idempotency_key=FIXED_IDEMPOTENCY_KEY,
        client_key=FIXED_CLIENT_KEY,
    ) == "v2=4494eaa27eeaa0095320f8293384f7dbd3c814d81b17c9e6c83cd0539615837a"


def test_signed_prospect_request_is_accepted() -> None:
    app, service = _build_app()
    body = _encoded_payload()

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body),
    )

    assert response.status_code == 201
    assert response.json() == {"registrationId": 42, "registrationIds": [42]}
    assert service.calls == 1
    assert len(service.idempotency_keys) == 1
    assert service.idempotency_keys[0] is not None
    assert len(service.request_hashes[0]) == 64


@pytest.mark.parametrize(
    ("error", "expected_fields"),
    [
        (
            VolunteerAlreadyExistsError(10232, "existing@example.com"),
            {"conflict_type": "existing_volunteer", "volunteer_id": 10232},
        ),
        (
            ActiveVolunteerRegistrationExistsError(77, "applicant@example.com"),
            {"conflict_type": "active_application", "registration_id": 77},
        ),
        (
            VolunteerApplicationFieldConflictError(
                "Friend conflict",
                {"friendEmails": {"0": "Already a volunteer."}},
                conflict_type="friend_email_existing_volunteer",
                volunteer_id=10232,
            ),
            {
                "conflict_type": "friend_email_existing_volunteer",
                "volunteer_id": 10232,
            },
        ),
        (
            VolunteerProspectIdempotencyConflictError(
                "Idempotency key was already used with different request content."
            ),
            {"conflict_type": "idempotency_key_content_mismatch"},
        ),
    ],
)
def test_prospect_conflicts_log_searchable_related_ids(
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected_fields: dict[str, object],
) -> None:
    app, service = _build_app()
    service.error = error
    body = _encoded_payload()

    with caplog.at_level(logging.WARNING, logger="app.api.v1.volunteer_prospects"):
        response = TestClient(app).post(
            VOLUNTEER_PROSPECT_PATH,
            content=body,
            headers=_signed_headers(body),
        )

    assert response.status_code == 409
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "volunteer.prospect.conflict"
    )
    assert all(record.event_data[key] == value for key, value in expected_fields.items())
    assert "existing@example.com" not in caplog.text
    assert "applicant@example.com" not in caplog.text


def test_legacy_v1_signed_prospect_request_remains_accepted_for_rollout() -> None:
    app, service = _build_app()
    body = _encoded_payload()

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body, version="v1"),
    )

    assert response.status_code == 201
    assert service.calls == 1


def test_unsigned_prospect_request_is_rejected() -> None:
    app, service = _build_app()

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        json=VALID_PAYLOAD,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Request authentication failed."}
    assert response.headers["WWW-Authenticate"] == "HMAC"
    assert service.calls == 0


def test_unsigned_malformed_body_is_authenticated_before_json_parsing() -> None:
    app, service = _build_app()

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 401
    assert service.calls == 0


def test_signature_does_not_authorize_an_altered_body() -> None:
    app, service = _build_app()
    signed_body = _encoded_payload()
    altered_body = _encoded_payload({**VALID_PAYLOAD, "phone": "+4799999999"})

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=altered_body,
        headers=_signed_headers(signed_body),
    )

    assert response.status_code == 401
    assert service.calls == 0


def test_signature_does_not_authorize_an_altered_idempotency_key() -> None:
    app, service = _build_app()
    body = _encoded_payload()
    headers = _signed_headers(body, idempotency_key=FIXED_IDEMPOTENCY_KEY)
    headers["X-Kvarteret-Idempotency-Key"] = str(uuid4())

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=headers,
    )

    assert response.status_code == 401
    assert service.calls == 0


def test_signature_does_not_authorize_an_altered_client_key() -> None:
    app, service = _build_app()
    body = _encoded_payload()
    headers = _signed_headers(body)
    headers["X-Kvarteret-Client-Key"] = "v1=" + "cd" * 32

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=headers,
    )

    assert response.status_code == 401
    assert service.calls == 0


def test_signed_oversized_body_is_rejected_before_json_parsing() -> None:
    app, service = _build_app(volunteer_prospect_max_body_bytes=32)
    body = _encoded_payload()

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body),
    )

    assert response.status_code == 413
    assert service.calls == 0


def test_signed_malformed_json_is_rejected_after_authentication() -> None:
    app, service = _build_app()
    body = b"not-json"

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body),
    )

    assert response.status_code == 422
    assert service.calls == 0


def test_oversized_field_is_rejected() -> None:
    app, service = _build_app()
    body = _encoded_payload({**VALID_PAYLOAD, "study_institution": "x" * 161})

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body),
    )

    assert response.status_code == 422
    assert service.calls == 0


def test_stale_signature_is_rejected() -> None:
    app, service = _build_app()
    body = _encoded_payload()
    stale_timestamp = str(int(time.time()) - 301)

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body, timestamp=stale_timestamp),
    )

    assert response.status_code == 401
    assert service.calls == 0


def test_replayed_nonce_is_rejected() -> None:
    app, service = _build_app()
    client = TestClient(app)
    body = _encoded_payload()
    headers = _signed_headers(body)

    first_response = client.post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=headers,
    )
    replayed_response = client.post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=headers,
    )

    assert first_response.status_code == 201
    assert replayed_response.status_code == 401
    assert service.calls == 1


def test_previous_secret_is_accepted_during_rotation() -> None:
    app, service = _build_app(previous_secret=PREVIOUS_HMAC_SECRET)
    body = _encoded_payload()

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body, secret=PREVIOUS_HMAC_SECRET),
    )

    assert response.status_code == 201
    assert service.calls == 1


def test_current_and_previous_signing_secrets_are_accepted() -> None:
    shared = "personal-signing-test-secret-0123456789"
    shared_previous = "personal-signing-previous-secret-01234"
    app, service = _build_app(
        personal_signing_secret=shared,
        personal_signing_previous_secret=shared_previous,
    )
    client = TestClient(app)

    for secret in (shared, shared_previous):
        body = _encoded_payload()
        response = client.post(
            VOLUNTEER_PROSPECT_PATH,
            content=body,
            headers=_signed_headers(body, secret=secret),
        )
        assert response.status_code == 201

    assert service.calls == 2


def test_shared_signing_secret_alone_is_enough() -> None:
    shared = "personal-signing-test-secret-0123456789"
    app, service = _build_app(secret=None, personal_signing_secret=shared)
    body = _encoded_payload()

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body, secret=shared),
    )

    assert response.status_code == 201
    assert service.calls == 1


def test_missing_hmac_configuration_fails_closed() -> None:
    app, service = _build_app(secret=None)
    body = _encoded_payload()

    response = TestClient(app).post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable."}
    assert service.calls == 0


def test_route_wide_rate_limit_survives_new_nonces() -> None:
    app, service = _build_app(volunteer_prospect_route_limit=1)
    client = TestClient(app)
    body = _encoded_payload()

    first = client.post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body),
    )
    second = client.post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body),
    )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "60"
    assert service.calls == 1


def test_client_rate_limit_is_keyed_by_signed_pseudonymous_client() -> None:
    app, service = _build_app(
        volunteer_prospect_route_limit=10,
        volunteer_prospect_client_limit=1,
    )
    client = TestClient(app)
    body = _encoded_payload()

    first = client.post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body),
    )
    second = client.post(
        VOLUNTEER_PROSPECT_PATH,
        content=body,
        headers=_signed_headers(body),
    )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "600"
    assert service.calls == 1


def test_same_email_is_not_rate_limited() -> None:
    app, service = _build_app(
        volunteer_prospect_route_limit=10,
        volunteer_prospect_client_limit=10,
    )
    client = TestClient(app)
    first_body = _encoded_payload()
    second_body = _encoded_payload({**VALID_PAYLOAD, "email": "KARI@example.com"})

    first = client.post(
        VOLUNTEER_PROSPECT_PATH,
        content=first_body,
        headers=_signed_headers(first_body),
    )
    second = client.post(
        VOLUNTEER_PROSPECT_PATH,
        content=second_body,
        headers=_signed_headers(second_body, client_key="v1=" + "cd" * 32),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert service.calls == 2


def test_field_validation_has_safe_code_and_specific_feedback(caplog):
    from app.domain.volunteer_applications.service import VolunteerApplicationFieldValidationError
    app, service = _build_app()
    service.error = VolunteerApplicationFieldValidationError("Sjekk venneadressene.", {"friendEmails": {"0": "E-postadressene må være ulike."}})
    body = json.dumps(VALID_PAYLOAD).encode()
    with caplog.at_level(logging.WARNING):
        response = TestClient(app).post(VOLUNTEER_PROSPECT_PATH, content=body, headers=_signed_headers(body))
    assert response.status_code == 400
    assert response.headers["X-Kvarteret-Rejection-Code"] == "field_validation"
    assert response.json()["detail"]["fieldErrors"]["friendEmails"]["0"] == "E-postadressene må være ulike."
    record = next(record for record in caplog.records if getattr(record, "event", None) == "volunteer.prospect.validation_failed")
    assert record.event_data["validation_fields"] == "friendEmails"
    assert record.event_data["validation_codes"] == "field_validation"
    assert "kari@example.com" not in json.dumps(record.event_data)
