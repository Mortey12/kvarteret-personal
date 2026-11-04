"""``POST /api/v1/leader/shifts/problem-reports`` ("Meld feil" in the leader app), with a fake sender."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth.roles import UserRole
from app.dependencies import get_feedback_service, get_shifts_service
from app.domain.feedback.service import (
    FeedbackDeliveryError,
    FeedbackRateLimitedError,
    FeedbackValidationError,
)
from app.domain.shifts.service import ShiftsService
from app.main import create_app
from tests.support.helpers import make_authenticated_user, override_authenticated_user
from tests.support.week_placing import PlacingRepository

ORIGIN = "http://localhost:3190"
URL = "/api/v1/leader/shifts/problem-reports"


class FakeFeedback:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.sent = error, []

    async def submit_feedback(self, **kwargs) -> None:
        if self.error:
            raise self.error
        self.sent.append(kwargs)


def make(monkeypatch, feedback: FakeFeedback, role: UserRole = UserRole.ADMIN):
    monkeypatch.setenv("LEADER_APP_ORIGINS", ORIGIN)
    app = create_app()
    user = make_authenticated_user(role)
    override_authenticated_user(app, user)
    service = ShiftsService(
        PlacingRepository(), now=lambda: datetime(2026, 11, 2, 12, tzinfo=UTC)
    )
    app.dependency_overrides[get_shifts_service] = lambda: service
    app.dependency_overrides[get_feedback_service] = lambda: feedback
    client = TestClient(app)
    me = client.get("/api/v1/leader/shifts/me")
    token = me.json().get("csrf_token", "x") if me.status_code == 200 else "x"
    return client, token, user


def send(client, token, body=None, **headers):
    base = {"Origin": ORIGIN, "X-CSRF-Token": token}
    base.update(headers)
    return client.post(
        URL,
        json=body or {"message": "Knappen virker ikke"},
        headers={k: v for k, v in base.items() if v},
    )


def test_a_report_becomes_a_bug_with_where_and_environment_but_no_name(
    monkeypatch,
) -> None:
    feedback = FakeFeedback()
    client, token, user = make(monkeypatch, feedback)

    response = send(
        client,
        token,
        {
            "message": "Knappen virker ikke",
            "group_id": 7,
            "week_start": "2026-11-02",
            "app_version": "2026-11-04",
            "data_source": "fake",
        },
    )

    assert response.status_code == 200
    assert "sendt" in response.json()["message"]
    (sent,) = feedback.sent
    assert sent["feedback_type"] == "bug" and sent["source"] == "skiftlista"
    assert sent["message"].startswith("[Skiftlista, test, fake] ")
    assert sent["page"] == "Skiftlista, bar 7, uke fra 2026-11-02"
    assert "2026-11-04" in sent["platform"]
    assert sent["name"] is None and sent["email"] is None
    assert sent["user_id"] == user.user_account_id
    everything = str(sent)
    assert user.display_name not in everything and user.email not in everything


@pytest.mark.parametrize("missing", ["Origin", "X-CSRF-Token"])
def test_the_safety_layer_is_required(monkeypatch, missing) -> None:
    feedback = FakeFeedback()
    client, token, _ = make(monkeypatch, feedback)

    response = send(client, token, **{missing: ""})

    assert response.status_code == 403
    assert feedback.sent == []


def test_a_bar_outside_the_scope_is_refused(monkeypatch) -> None:
    feedback = FakeFeedback()
    client, token, _ = make(monkeypatch, feedback, UserRole.GROUP_ADMIN)

    response = send(client, token, {"message": "x", "group_id": 999})

    assert response.status_code == 403
    assert feedback.sent == []


def test_a_volunteer_cannot_report(monkeypatch) -> None:
    client, token, _ = make(monkeypatch, FakeFeedback(), UserRole.VOLUNTEER)
    assert send(client, token).status_code == 403


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (FeedbackDeliveryError("LINEAR_API_KEY is not configured."), 503),
        (FeedbackRateLimitedError("Too many"), 429),
        (FeedbackValidationError("For langt."), 422),
    ],
)
def test_failures_say_so(monkeypatch, error, code) -> None:
    client, token, _ = make(monkeypatch, FakeFeedback(error))

    response = send(client, token)

    assert response.status_code == code
    if code == 503:
        assert "ikke sendt" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"message": ""},
        {"message": "x" * 1501},
        {"message": "x", "app_version": "<script>"},
        {"message": "x", "data_source": "other"},
        {"message": "x", "group_id": 2**40},
        {"message": "x", "extra": 1},
    ],
)
def test_bad_input_is_refused_before_anything_is_sent(monkeypatch, body) -> None:
    feedback = FakeFeedback()
    client, token, _ = make(monkeypatch, feedback)

    assert send(client, token, body).status_code == 422
    assert feedback.sent == []
