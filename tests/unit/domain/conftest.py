from __future__ import annotations

import pytest

from tests.support.shift_groups import shift_groups_session


@pytest.fixture
async def session(monkeypatch):
    async for db in shift_groups_session(monkeypatch):
        yield db
