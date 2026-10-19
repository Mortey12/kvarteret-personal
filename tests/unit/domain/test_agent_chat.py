from __future__ import annotations

from datetime import date

import pytest

from app.config import Settings
from app.domain.agent_chat.service import (
    AgentChatService,
    InvalidProposalError,
)


class FakeRepo:
    def __init__(self):
        self.updated = None
        self.contract = None
        self.volunteer = {
            "id": 1, "first_name": "Kari", "last_name": "Nordmann", "email": None,
            "phone": None, "birth_date": date(2000, 1, 2), "address": None, "postal_code": None,
        }

    async def get_volunteer(self, volunteer_id):
        return self.volunteer if volunteer_id == 1 else None

    async def get_assignment(self, assignment_id):
        return {"id": 9, "volunteer_id": 1, "contract_signed": False} if assignment_id == 9 else None

    async def update_volunteer_fields(self, volunteer_id, values):
        self.updated = (volunteer_id, values)

    async def set_contract_signed(self, assignment_id, signed):
        self.contract = (assignment_id, signed)


def make(repo=None, invalidated=None):
    settings = Settings(app_secret_key="k", azure_openai_endpoint="https://x", azure_openai_api_key="y")
    return AgentChatService(settings, repo or FakeRepo(), (invalidated if invalidated is not None else []).append)


async def test_proposal_changes_nothing_until_applied():
    repo, seen = FakeRepo(), []
    svc = make(repo, seen)
    result = await svc._run_tool(
        "propose_update_volunteer",
        '{"volunteer_id": 1, "changes": {"email": " a@b.no ", "birth_date": "2001-02-03"}}',
        5,
    )
    assert repo.updated is None
    token = result["proposal"]["token"]
    await svc.apply(token, admin_id=5)
    assert repo.updated == (1, {"email": "a@b.no", "birth_date": date(2001, 2, 3)})
    assert seen == [1]


async def test_apply_rejects_other_admin_and_tampering():
    svc = make()
    token = (await svc._run_tool("propose_set_contract_signed", '{"assignment_id": 9, "signed": true}', 5))["proposal"]["token"]
    with pytest.raises(InvalidProposalError):
        await svc.apply(token, admin_id=6)
    with pytest.raises(InvalidProposalError):
        await svc.apply(token + "x", admin_id=5)


async def test_rejects_unknown_fields_and_noop():
    svc = make()
    assert "error" in await svc._run_tool("propose_update_volunteer", '{"volunteer_id": 1, "changes": {"role": "Admin"}}', 5)
    assert "error" in await svc._run_tool("propose_update_volunteer", '{"volunteer_id": 1, "changes": {"first_name": "Kari"}}', 5)
    assert "error" in await svc._run_tool("nope", "{}", 5)
