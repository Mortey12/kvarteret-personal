from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.roles import UserRole
from app.main import create_app
from tests.support.helpers import (
    csrf_headers,
    make_authenticated_user,
    override_authenticated_user,
    prime_csrf,
)


@pytest.mark.parametrize("role", [UserRole.GROUP_ADMIN, UserRole.VOLUNTEER])
def test_agent_is_admin_only(role):
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(role))
    client = TestClient(app)
    prime_csrf(client)
    assert client.get("/agent").status_code == 403
    response = client.post(
        "/agent/chat",
        json={"messages": [{"role": "user", "content": "hei"}]},
        headers=csrf_headers(client),
    )
    assert response.status_code == 403
    assert client.post("/agent/apply", json={"token": "x"}, headers=csrf_headers(client)).status_code == 403


def test_catalog_lists_admin_actions_and_excludes_sensitive_ones():
    from app.web.routes.agent.actions import AdminActionCatalog

    catalog = AdminActionCatalog(create_app())
    listed = {(a["method"], a["path"]) for a in catalog.list(None)}
    assert ("PATCH", "/volunteers/{volunteer_id}") in listed
    assert ("DELETE", "/groups/{group_id}") in listed
    assert ("POST", "/courses") in listed
    for method, path in listed:
        assert "impersonat" not in path and not path.startswith(("/api/", "/agent", "/my-account"))
        assert path not in {"/login", "/logout", "/feedback"}
    assert catalog.resolve("POST", "/courses", {"name": ["x"]})["danger"] is False
    assert catalog.resolve("DELETE", "/courses/3", {})["danger"] is True
    assert "error" in catalog.resolve("POST", "/courses", {"bogus": ["x"]})
    assert "error" in catalog.resolve("POST", "/admin-accounts/1/impersonate", {})
    assert "error" in catalog.resolve("POST", "/login", {})


def test_confirmed_action_runs_through_real_route_as_admin():
    from app.dependencies import get_courses_service

    created = []

    class FakeCourses:
        async def create_course(self, *, name, description):
            created.append((name, description))
            return 7

    app = create_app()
    admin = make_authenticated_user(UserRole.ADMIN)
    override_authenticated_user(app, admin)
    # Management routes read the user via get_current_user/require_authenticated_user.
    app.dependency_overrides[get_courses_service] = lambda: FakeCourses()
    client = TestClient(app)
    prime_csrf(client)
    service = app.state.container.agent_chat_service
    resolved = {"kind": "action", "method": "POST", "path": "/courses", "form": {"name": ["Brannvern"]}}
    token = service._sign(admin.user_account_id, resolved)
    response = client.post("/agent/apply", json={"token": token}, headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    assert created == [("Brannvern", None)]
    assert "/courses/7" in response.json()["message"]
