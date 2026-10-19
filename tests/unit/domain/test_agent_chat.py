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
        return {"id": 9, "volunteer_id": 1, "contract_signed": False, "semester": 20262, "group": "Bar", "role": "Vert"} if assignment_id == 9 else None

    async def update_volunteer_fields(self, volunteer_id, values):
        self.updated = (volunteer_id, values)

    async def set_contract_signed(self, assignment_id, signed):
        self.contract = (assignment_id, signed)


async def counter(only_active=False):
    return 3 if only_active else 10


async def searcher(query, limit):
    return []


class FakeAssignments:
    def __init__(self):
        self.calls = []

    async def delete_role_assignment_for_volunteer(self, volunteer_id, history_id):
        self.calls.append(("delete", volunteer_id, history_id))

    async def add_role_assignment(self, **kw):
        self.calls.append(("add", kw))


def make(repo=None, invalidated=None, assignments=None):
    settings = Settings(app_secret_key="k", azure_openai_endpoint="https://x", azure_openai_api_key="y")
    return AgentChatService(settings, repo or FakeRepo(), (invalidated if invalidated is not None else []).append, counter, searcher, assignments)


async def test_proposal_changes_nothing_until_applied():
    repo, seen = FakeRepo(), []
    svc = make(repo, seen)
    result = await svc._run_tool(
        "propose_update_volunteer",
        '{"volunteer_id": 1, "changes": [{"field": "email", "value": " a@b.no "}, {"field": "birth_date", "value": "2001-02-03"}]}',
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
    assert "error" in await svc._run_tool("propose_update_volunteer", '{"volunteer_id": 1, "changes": [{"field": "role", "value": "Admin"}]}', 5)
    assert "error" in await svc._run_tool("propose_update_volunteer", '{"volunteer_id": 1, "changes": [{"field": "first_name", "value": "Kari"}]}', 5)
    assert "error" in await svc._run_tool("nope", "{}", 5)


async def test_stats_tool_matches_stats_endpoint_shape():
    assert await make()._run_tool("get_volunteer_stats", "{}", 5) == {
        "totalVolunteers": 10,
        "currentSemesterVolunteers": 3,
    }


def test_check_select_guards():
    from app.domain.agent_chat.repository import SelectRejectedError, check_select

    assert check_select("select 1;") == "select 1"
    assert check_select("WITH a AS (select 1) select * from a")
    for bad in ["delete from groups", "select 1; select 2", "select pg_sleep(1)",
                "select set_config('role','none',true)", "copy x to stdout"]:
        with pytest.raises(SelectRejectedError):
            check_select(bad)


async def test_remove_assignment_only_after_confirmation():
    assignments = FakeAssignments()
    svc = make(assignments=assignments)
    result = await svc._run_tool("propose_remove_assignment", '{"assignment_id": 9}', 5)
    assert assignments.calls == []
    assert "Bar / Vert 2026H" in result["proposal"]["title"]
    await svc.apply(result["proposal"]["token"], admin_id=5)
    assert assignments.calls == [("delete", 1, 9)]


async def test_update_schema_has_no_implicit_nulls():
    from app.domain.agent_chat.service import TOOLS

    tool = next(t for t in TOOLS if t["name"] == "propose_update_volunteer")
    assert tool["parameters"]["properties"]["changes"]["type"] == "array"
