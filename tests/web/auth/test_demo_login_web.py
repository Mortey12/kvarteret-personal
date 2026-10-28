"""The demo leader login at /login/demo and the demo banner (hosted demo with fake data only)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.demo_login import DemoLoginRefused
from app.auth.models import UserAccount
from app.auth.roles import UserRole
from app.config import Settings
from app.db.rate_limit import InMemoryRateLimiter
from app.dependencies import (
    get_auth_repository,
    get_current_user,
    get_demo_guard,
    get_rate_limiter,
    get_session_store,
)
from app.main import create_app
from app.runtime import build_application_container
from tests.support.helpers import csrf_headers, make_authenticated_user, prime_csrf

CODE = "demo-kode-1234"


def _leader(role: UserRole = UserRole.ADMIN) -> UserAccount:
    return UserAccount(
        id=77, auth_user_id=uuid4(), username="demo.leder", email="demo.leder@example.dev",
        display_name="Demo Leder", role=role, last_login=None,
    )


def _client(*, enabled: bool):
    settings = Settings(demo_login_enabled=True, demo_access_code=CODE) if enabled else Settings()
    app = create_app(build_application_container(settings))
    guard = SimpleNamespace(ensure_only_fake_people=AsyncMock())
    accounts = SimpleNamespace(get_user_account_by_identifier=AsyncMock(return_value=_leader()))
    sessions = SimpleNamespace(create_session=AsyncMock(return_value=SimpleNamespace(session_id="web-session")))
    app.dependency_overrides[get_current_user] = lambda: None
    app.dependency_overrides[get_demo_guard] = lambda: guard
    app.dependency_overrides[get_auth_repository] = lambda: accounts
    app.dependency_overrides[get_session_store] = lambda: sessions
    rate_limiter = InMemoryRateLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: rate_limiter
    client = TestClient(app)
    prime_csrf(client, path="/login")
    return client, SimpleNamespace(guard=guard, accounts=accounts, sessions=sessions)


@pytest.fixture
def enabled():
    return _client(enabled=True)


@pytest.fixture
def disabled():
    return _client(enabled=False)


def _post(client, code: str = CODE, **extra):
    return client.post(
        "/login/demo", data={"access_code": code, **extra}, headers=csrf_headers(client), follow_redirects=False
    )


def test_off_by_default_there_is_no_demo_login_or_banner(disabled):
    client, fakes = disabled
    page = client.get("/login")
    assert 'action="/login/demo"' not in page.text
    assert "data-demo-banner" not in page.text
    assert _post(client).status_code == 404
    fakes.sessions.create_session.assert_not_awaited()


def test_the_login_page_shows_the_demo_form_and_banner(enabled):
    client, _ = enabled
    page = client.get("/login")
    assert 'action="/login/demo"' in page.text
    assert "data-demo-banner" in page.text
    assert "Demo, falske data" in page.text
    assert CODE not in page.text


def test_the_code_logs_in_the_demo_leader_as_admin(enabled, caplog):
    client, fakes = enabled
    with caplog.at_level("INFO"):
        response = _post(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/shifts"
    assert "kvarteret_session" in response.headers["set-cookie"]
    fakes.guard.ensure_only_fake_people.assert_awaited_once()
    fakes.accounts.get_user_account_by_identifier.assert_awaited_once_with("demo.leder@example.dev")
    assert fakes.sessions.create_session.call_args.kwargs["user_account_id"] == 77
    demo_logs = [r for r in caplog.records if r.event == "auth.demo_login.succeeded"]
    assert [(r.event_data["demo_account"], r.event_data["login_kind"]) for r in demo_logs] == [("demo.leder", "leader")]
    assert CODE not in caplog.text


@pytest.mark.parametrize("code", ["feil-kode-1234", CODE.upper(), "", "123456"])
def test_a_wrong_code_is_refused(enabled, code):
    client, fakes = enabled
    response = _post(client, code)
    assert response.status_code in {401, 422}
    fakes.sessions.create_session.assert_not_awaited()
    fakes.accounts.get_user_account_by_identifier.assert_not_awaited()


def test_a_leader_account_without_admin_gets_no_session(enabled):
    client, fakes = enabled
    fakes.accounts.get_user_account_by_identifier.return_value = _leader(UserRole.GROUP_ADMIN)
    assert _post(client).status_code == 503
    fakes.accounts.get_user_account_by_identifier.return_value = None
    assert _post(client).status_code == 503
    fakes.sessions.create_session.assert_not_awaited()


def test_no_session_when_the_database_holds_real_people(enabled):
    client, fakes = enabled
    fakes.guard.ensure_only_fake_people.side_effect = DemoLoginRefused("real people")
    assert _post(client).status_code == 503
    fakes.sessions.create_session.assert_not_awaited()


def test_repeated_wrong_codes_are_throttled(enabled):
    client, fakes = enabled
    statuses = [_post(client, "feil-kode-1234").status_code for _ in range(11)]
    assert statuses[-1] == 429
    assert _post(client).status_code == 429
    fakes.sessions.create_session.assert_not_awaited()


def test_the_demo_login_requires_csrf(enabled):
    client, fakes = enabled
    assert client.post("/login/demo", data={"access_code": CODE}).status_code == 403
    fakes.sessions.create_session.assert_not_awaited()


@pytest.mark.parametrize("on", [True, False])
def test_the_banner_shows_on_leader_pages_only_when_enabled(on):
    client, _ = _client(enabled=on)
    client.app.dependency_overrides[get_current_user] = lambda: make_authenticated_user(UserRole.ADMIN)
    page = client.get("/")
    assert page.status_code == 200
    assert ("data-demo-banner" in page.text) is on
