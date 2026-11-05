from __future__ import annotations

from types import SimpleNamespace

from app.domain.shifts.models import QualificationSettings
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.auth.roles import UserRole
from app.dependencies import get_current_user, get_shift_scope, get_shifts_service
from app.domain.shifts.errors import ShiftForbiddenError
from app.domain.shifts.permissions import ALL_GROUPS, GroupScope
from app.domain.shifts.week_page import WeekPage
from app.main import create_app
from tests.support.helpers import make_authenticated_user, override_authenticated_user
from tests.support.leader_week import leader_week_view

ME = "/api/v1/leader/shifts/me"
WEEK = "/api/v1/leader/shifts/weeks/2026-11-04"
BARS = [
    SimpleNamespace(group_id=7, name="Stjerne"),
    SimpleNamespace(group_id=9, name="Grøndahls"),
]


@pytest.fixture
def setup(monkeypatch):
    app = create_app()
    service = SimpleNamespace(
        repository=SimpleNamespace(
            list_shift_groups=AsyncMock(return_value=BARS),
            list_group_qualifications=AsyncMock(return_value=[]),
            get_qualification_settings=AsyncMock(
                side_effect=lambda ids: {i: QualificationSettings() for i in ids}
            ),
        ),
        week_changes=AsyncMock(return_value={}),
        list_suggestions=AsyncMock(return_value=[]),
    )
    app.dependency_overrides[get_shifts_service] = lambda: service
    app.dependency_overrides[get_shift_scope] = lambda: ALL_GROUPS
    override_authenticated_user(app, make_authenticated_user(UserRole.ADMIN))
    calls: list[dict] = []

    async def fake_load(service_arg, *, week, group_id, scope, today=None, allow_unqualified=False):
        calls.append({"week": week, "group_id": group_id, "allow_unqualified": allow_unqualified})
        return WeekPage(bars=[], view=leader_week_view())

    monkeypatch.setattr("app.api.v1.leader_shifts.load_week_page", fake_load)
    return app, calls


def test_me_lists_the_bars_in_scope_and_hands_out_the_csrf_token(setup):
    app, _ = setup
    app.dependency_overrides[get_shift_scope] = lambda: GroupScope(frozenset({7}))
    response = TestClient(app).get(ME)

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "Admin" and body["bars"] == [{"id": 7, "name": "Stjerne"}]
    assert body["csrf_token"]
    assert response.headers["cache-control"] == "private, no-store"


def test_week_returns_the_board_and_passes_the_arguments_on(setup):
    app, calls = setup
    response = TestClient(app).get(WEEK + "?group_id=7")

    assert response.status_code == 200
    body = response.json()
    assert body["bar"] == {"id": 7, "name": "Stjerne"} and body["week_start"] == "2026-11-02"
    assert len(body["shifts"]) == 2 and body["version"]
    assert calls[0]["group_id"] == 7 and str(calls[0]["week"]) == "2026-11-04"
    # A leader may place anyone after confirming a warning, so the board is built leniently.
    assert calls[0]["allow_unqualified"] is True


def test_a_bar_outside_the_scope_is_403_with_the_servers_text(setup, monkeypatch):
    app, _ = setup

    async def refuse(*args, **kwargs):
        raise ShiftForbiddenError("Du har ikke tilgang til å se skiftplanen for denne gruppa.")

    monkeypatch.setattr("app.api.v1.leader_shifts.load_week_page", refuse)
    response = TestClient(app).get(WEEK + "?group_id=9")

    assert response.status_code == 403
    assert response.json() == {"detail": "Du har ikke tilgang til å se skiftplanen for denne gruppa."}


def test_no_bar_in_scope_is_404(setup, monkeypatch):
    app, _ = setup

    async def nothing(*args, **kwargs):
        return WeekPage(bars=[])

    monkeypatch.setattr("app.api.v1.leader_shifts.load_week_page", nothing)

    assert TestClient(app).get(WEEK).status_code == 404


def test_a_bad_date_is_422(setup):
    app, _ = setup
    assert TestClient(app).get("/api/v1/leader/shifts/weeks/not-a-date").status_code == 422


@pytest.mark.parametrize("path", [ME, WEEK])
def test_not_signed_in_is_401(path):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: None
    assert TestClient(app).get(path).status_code == 401


@pytest.mark.parametrize("role", [UserRole.VOLUNTEER, UserRole.VIEWER])
@pytest.mark.parametrize("path", [ME, WEEK])
def test_other_roles_are_refused(setup, role, path):
    app, _ = setup
    app.dependency_overrides.pop(get_shift_scope, None)
    override_authenticated_user(app, make_authenticated_user(role))
    assert TestClient(app).get(path).status_code == 403
