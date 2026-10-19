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
