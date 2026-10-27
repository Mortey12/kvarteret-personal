"""HTTP boundary for Skiftlista in the app: session auth and error mapping.

The rules themselves are tested against a database in
tests/unit/domain/test_shift_app_service.py.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_mobile_card_service, get_shifts_app_service
from app.domain.mobile_card.errors import MobileCardInvalidSessionError
from app.domain.mobile_card.models import MobileCardSessionIdentity
from app.domain.mobile_card.service import MobileCardService
from app.domain.shifts.app_models import (
    ClaimPlaceRequest,
    MyShift,
    ShiftActionResponse,
    ShiftAppConfigResponse,
)
from app.domain.shifts.errors import ShiftAccessError, ShiftNotFoundError, ShiftPlacementError
from app.main import create_app

BASE = "/api/v1/mobile-card/shifts"
AUTH = {"Authorization": "Bearer good"}


class FakeMobileCardService:
    def __init__(self) -> None:
        self.identities = {
            "good": MobileCardSessionIdentity(11, False, False, None),
            "renew": MobileCardSessionIdentity(11, False, False, "fresh-token"),
            "review": MobileCardSessionIdentity(None, True, False, None),
        }

    def authenticate_session(self, token: str) -> MobileCardSessionIdentity:
        if token not in self.identities:
            raise MobileCardInvalidSessionError("Unknown session token.", reason="bad_signature")
        return self.identities[token]


def _my_shift() -> MyShift:
    start = datetime(2026, 11, 3, 10, 30, tzinfo=UTC)
    return MyShift(
        shift_id=1,
        group_id=1,
        group_name="Stjerne",
        venue="Stjernesalen",
        venue_label="Stjernesalen og kjøkken",
        title="Dagskift",
        starts_at=start,
        ends_at=start.replace(hour=16),
        day=date(2026, 11, 3),
        day_label="Tirsdag 3. november",
        time_label="11:30–17:00",
        note=None,
        min_age=None,
        assignment_id=5,
        slot_id=2,
        kind="skjenker",
        label="Skjenker",
        tag=None,
        counts_as_samfunnettjeneste=False,
        swap_requested=False,
        cannot_attend=False,
        can_request_swap=True,
        can_report_cannot_attend=False,
        meeting_point=None,
        description=None,
        crew=[],
    )


class FakeShiftsAppService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def get_config(self, volunteer_id):
        self.calls.append(("config", volunteer_id))
        return ShiftAppConfigResponse(
            enabled=volunteer_id is not None,
            audience="skjenke" if volunteer_id else "none",
            tabs=[],
            groups=[],
            slot_kinds=[],
            wish_answers=[],
            weekdays=[],
            courses=[],
            samf_target=2,
        )

    async def list_mine(self, volunteer_id):
        raise ShiftAccessError("Skiftlista er ikke slått på for deg ennå.")

    async def claim(self, volunteer_id, slot_id, request: ClaimPlaceRequest):
        self.calls.append(("claim", volunteer_id, slot_id, request.course_session_ids))
        if slot_id == 404:
            raise ShiftNotFoundError("Finner ikke plassen.")
        if slot_id == 409:
            raise ShiftPlacementError("Plassen ble nettopp tatt.")
        return ShiftActionResponse(message="Du er satt opp.", shift=_my_shift())

    async def set_weekday_rule(self, volunteer_id, weekday, answer):
        self.calls.append(("rule", weekday, answer))
        raise ShiftPlacementError("stop")


def _client() -> tuple[TestClient, FakeShiftsAppService]:
    app = create_app()
    service = FakeShiftsAppService()
    app.dependency_overrides[get_mobile_card_service] = lambda: FakeMobileCardService()
    app.dependency_overrides[get_shifts_app_service] = lambda: service
    return TestClient(app), service


def test_requires_a_valid_bearer_token() -> None:
    client, _ = _client()
    assert client.get(f"{BASE}/config").status_code == 401
    response = client.get(f"{BASE}/config", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_config_for_review_session_is_disabled_not_forbidden() -> None:
    client, service = _client()
    response = client.get(f"{BASE}/config", headers={"Authorization": "Bearer review"})
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert service.calls == [("config", None)]


def test_other_calls_need_a_volunteer() -> None:
    client, _ = _client()
    response = client.get(f"{BASE}/mine", headers={"Authorization": "Bearer review"})
    assert response.status_code == 403


def test_renewed_session_token_is_returned() -> None:
    client, _ = _client()
    response = client.get(f"{BASE}/config", headers={"Authorization": "Bearer renew"})
    assert response.headers["X-Mobile-Card-Session-Token"] == "fresh-token"


def test_errors_map_to_status_codes_with_text_to_show() -> None:
    client, _ = _client()
    assert client.get(f"{BASE}/mine", headers=AUTH).json() == {
        "detail": "Skiftlista er ikke slått på for deg ennå."
    }
    assert client.get(f"{BASE}/mine", headers=AUTH).status_code == 403
    assert client.post(f"{BASE}/places/404/claim", headers=AUTH).status_code == 404
    refused = client.post(f"{BASE}/places/409/claim", headers=AUTH)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Plassen ble nettopp tatt."


def test_claim_body_is_optional_and_strict() -> None:
    client, service = _client()
    assert client.post(f"{BASE}/places/2/claim", headers=AUTH).status_code == 200
    response = client.post(f"{BASE}/places/2/claim", headers=AUTH, json={"course_session_ids": [7, 8]})
    assert response.status_code == 200
    assert response.json()["shift"]["assignment_id"] == 5
    assert service.calls[-2:] == [("claim", 11, 2, []), ("claim", 11, 2, [7, 8])]
    assert client.post(f"{BASE}/places/2/claim", headers=AUTH, json={"oops": 1}).status_code == 422


def test_weekday_must_be_0_to_6_and_answer_known() -> None:
    client, service = _client()
    assert client.put(f"{BASE}/wishes/weekdays/7", headers=AUTH, json={"answer": "vil"}).status_code == 422
    assert client.put(f"{BASE}/wishes/weekdays/1", headers=AUTH, json={"answer": "kanskje"}).status_code == 422
    assert client.put(f"{BASE}/wishes/weekdays/1", headers=AUTH, json={"answer": None}).status_code == 409
    assert service.calls == [("rule", 1, None)]


def test_session_identity_from_real_tokens() -> None:
    service = MobileCardService(
        Settings(app_env="test", app_secret_key="test-secret-key-for-shift-identity"),
        repository=None,  # type: ignore[arg-type]
        email_sender=None,  # type: ignore[arg-type]
        rate_limiter=None,  # type: ignore[arg-type]
    )
    volunteer = service.authenticate_session(service.sessions.build_token({"person_id": 42}))
    assert volunteer.volunteer_id == 42 and not volunteer.is_trial and volunteer.renewed_session_token is None
    trial = service.authenticate_session(service.sessions.build_token({"trial_application_id": 7}))
    assert trial.volunteer_id is None and trial.is_trial
    review = service.authenticate_session(service.sessions.build_token({"person_id": 0, "review": True}))
    assert review.volunteer_id is None and review.is_review
    with pytest.raises(MobileCardInvalidSessionError):
        service.authenticate_session("not-a-token")
