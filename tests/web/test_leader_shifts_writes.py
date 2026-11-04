"""The leader app's writes, end to end: HTTP layer, the real ``ShiftsService`` and the in-memory
repository of ``tests/support/week_placing.py``. Made-up people only. Week of Monday 2026-11-02.

What is checked: the safety layer (Origin, CSRF, If-Match), that the old rules still hold, that
``confirmed`` skips only the qualification rule, scope, swap being all or nothing, and the audit log.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth.roles import UserRole
from app.dependencies import get_shifts_service
from app.domain.shifts.service import ShiftsService
from app.main import create_app
from tests.support.helpers import make_authenticated_user, override_authenticated_user
from tests.support.week_placing import (
    HALLE,
    SANDER,
    SECOND_SHIFT_SLOT,
    SIGRID,
    SKIFTLEDER_SLOT,
    SKJENKER_SLOT,
    UNDINE,
    PlacingRepository,
)

ORIGIN = "http://localhost:3190"
BASE = "/api/v1/leader/shifts"
WEEK = "2026-11-02"
CONTEXT = {"group_id": 7, "week_start": WEEK}


@pytest.fixture
def audit(monkeypatch):
    entries: list[dict] = []
    monkeypatch.setattr("app.api.v1.leader_shifts.log_admin_activity", lambda **kwargs: entries.append(kwargs))
    return entries


def make(monkeypatch, role: UserRole = UserRole.ADMIN, repo: PlacingRepository | None = None):
    monkeypatch.setenv("LEADER_APP_ORIGINS", ORIGIN)
    repo = repo or PlacingRepository()
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(role))
    service = ShiftsService(repo, now=lambda: datetime(2026, 11, 2, 12, tzinfo=UTC))
    app.dependency_overrides[get_shifts_service] = lambda: service
    client = TestClient(app)
    me = client.get(f"{BASE}/me").json()
    return client, repo, me["csrf_token"]


class Writer:
    """Sends writes the way the leader app does: Origin, CSRF token and the board's version."""

    def __init__(self, client: TestClient, token: str) -> None:
        self.client, self.token = client, token

    def version(self, **params) -> str:
        return self.client.get(f"{BASE}/weeks/{WEEK}", params={"group_id": 7, **params}).json()["version"]

    def headers(self, **override) -> dict[str, str]:
        base = {"Origin": ORIGIN, "X-CSRF-Token": self.token}
        base["If-Match"] = override.pop("If-Match") if "If-Match" in override else self.version()
        base.update(override)
        return {key: value for key, value in base.items() if value is not None}

    def send(self, method: str, path: str, json=None, params=None, **override):
        return self.client.request(
            method, f"{BASE}{path}", json=json, params={**CONTEXT, **(params or {})}, headers=self.headers(**override)
        )


@pytest.fixture
def writer(monkeypatch, audit):
    client, repo, token = make(monkeypatch)
    return Writer(client, token), repo


# ---- the safety layer ---------------------------------------------------------------------------


def test_a_write_needs_an_allowed_origin(writer):
    w, repo = writer
    body = {"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER}
    for origin in (None, "https://evil.example", "http://localhost:3191"):
        response = w.send("POST", "/placements", json=body, Origin=origin)
        assert response.status_code == 403
    assert repo.who(SKJENKER_SLOT) == []


def test_a_write_needs_the_csrf_token_that_matches_the_cookie(writer):
    w, repo = writer
    body = {"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER}
    assert w.send("POST", "/placements", json=body, **{"X-CSRF-Token": None}).status_code == 403
    assert w.send("POST", "/placements", json=body, **{"X-CSRF-Token": "forged"}).status_code == 403
    assert repo.who(SKJENKER_SLOT) == []


def test_no_origin_is_allowed_when_none_are_configured(monkeypatch):
    monkeypatch.setenv("LEADER_APP_ORIGINS", "")
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(UserRole.ADMIN))
    repo = PlacingRepository()
    service = ShiftsService(repo, now=lambda: datetime(2026, 11, 2, 12, tzinfo=UTC))
    app.dependency_overrides[get_shifts_service] = lambda: service
    client = TestClient(app)
    token = client.get(f"{BASE}/me").json()["csrf_token"]
    response = client.post(
        f"{BASE}/placements", params=CONTEXT, json={"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER},
        headers={"Origin": ORIGIN, "X-CSRF-Token": token, "If-Match": "x"},
    )
    assert response.status_code == 403
    assert repo.who(SKJENKER_SLOT) == []


def test_a_stale_version_is_a_409_and_changes_nothing(writer):
    w, repo = writer
    stale = w.version()
    repo.place(SKJENKER_SLOT, UNDINE)  # another leader changed the week meanwhile
    response = w.send("POST", "/placements", json={"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER}, **{"If-Match": stale})
    assert response.status_code == 409
    assert response.json()["detail"] == "En annen leder har endret uka. Siden er oppdatert, prøv igjen."
    assert repo.who(SKJENKER_SLOT) == [UNDINE]


def test_if_match_is_required(writer):
    w, repo = writer
    response = w.client.post(
        f"{BASE}/placements", params=CONTEXT, json={"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER},
        headers={"Origin": ORIGIN, "X-CSRF-Token": w.token},
    )
    assert response.status_code == 422
    assert repo.who(SKJENKER_SLOT) == []


@pytest.mark.parametrize("role", [UserRole.VOLUNTEER, UserRole.VIEWER])
def test_other_roles_cannot_write(monkeypatch, audit, role):
    client, repo, _ = make(monkeypatch, UserRole.ADMIN)
    override_authenticated_user(client.app, make_authenticated_user(role))
    response = client.post(
        f"{BASE}/placements", params=CONTEXT, json={"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER},
        headers={"Origin": ORIGIN, "X-CSRF-Token": "x", "If-Match": "x"},
    )
    assert response.status_code == 403
    assert repo.who(SKJENKER_SLOT) == []


# ---- place, replace, move, remove ----------------------------------------------------------------


def test_place_puts_the_person_on_the_place_and_answers_with_the_refreshed_week(writer, audit):
    w, repo = writer
    before = w.version()
    response = w.send("POST", "/placements", json={"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER})

    assert response.status_code == 200
    body = response.json()
    assert repo.who(SKJENKER_SLOT) == [SANDER]
    assert body["message"].startswith("Sander Skjenker er satt opp som skjenker")
    assert body["board"]["version"] != before
    placed = [p for s in body["board"]["shifts"] for p in s["places"] if p["assignment"]]
    assert [p["assignment"]["volunteer_id"] for p in placed] == [SANDER]
    assert audit[-1]["action"] == "shift.assign" and audit[-1]["details"]["via"] == "leader-app"


def test_a_person_without_the_approval_is_refused_unless_the_leader_confirmed(writer, audit):
    w, repo = writer
    body = {"slot_id": SKIFTLEDER_SLOT, "volunteer_id": SANDER}

    refused = w.send("POST", "/placements", json=body)
    assert refused.status_code == 409
    assert "skiftleder" in refused.json()["detail"].lower()
    assert repo.who(SKIFTLEDER_SLOT) == []

    accepted = w.send("POST", "/placements", json={**body, "confirmed": True})
    assert accepted.status_code == 200
    assert repo.who(SKIFTLEDER_SLOT) == [SANDER]
    # The log keeps only a fixed set of fields, so the confirmation is part of the action name.
    assert audit[-1]["action"] == "shift.assign.confirmed"


def test_confirmed_never_skips_the_other_rules(writer):
    w, repo = writer
    # Already on the shift.
    repo.place(SKJENKER_SLOT, SANDER)
    r = w.send("POST", "/placements", json={"slot_id": SKIFTLEDER_SLOT, "volunteer_id": SANDER, "confirmed": True})
    assert r.status_code == 409 and "allerede" in r.json()["detail"]
    # A full place (two seats).
    repo.place(SKJENKER_SLOT, UNDINE)
    r = w.send("POST", "/placements", json={"slot_id": SKJENKER_SLOT, "volunteer_id": HALLE, "confirmed": True})
    assert r.status_code == 409 and "full" in r.json()["detail"].lower()
    # A closed shift.
    repo.shifts[2].is_closed = True
    r = w.send("POST", "/placements", json={"slot_id": SECOND_SHIFT_SLOT, "volunteer_id": HALLE, "confirmed": True})
    assert r.status_code == 409 and "stengt" in r.json()["detail"]
    assert repo.who(SECOND_SHIFT_SLOT) == []


def test_a_gruppeadmin_cannot_write_to_a_bar_outside_the_scope(monkeypatch, audit):
    repo = PlacingRepository()
    repo.admin_group_ids = [8]  # the kitchen only
    client, repo, token = make(monkeypatch, UserRole.GROUP_ADMIN, repo)
    w = Writer(client, token)
    response = w.send(
        "POST", "/placements", json={"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER}, **{"If-Match": "x"}
    )
    assert response.status_code == 403
    assert repo.who(SKJENKER_SLOT) == []


def test_replace_swaps_one_person_for_another_on_the_same_place(writer):
    w, repo = writer
    old = repo.place(SKJENKER_SLOT, SANDER)
    response = w.send("PUT", f"/placements/{old}", json={"slot_id": SKJENKER_SLOT, "volunteer_id": UNDINE})
    assert response.status_code == 200
    assert repo.who(SKJENKER_SLOT) == [UNDINE]
    assert "i stedet for Sander" in response.json()["message"]


def test_replace_with_an_unqualified_person_needs_confirmed(writer, audit):
    w, repo = writer
    old = repo.place(SKIFTLEDER_SLOT, SIGRID)
    body = {"slot_id": SKIFTLEDER_SLOT, "volunteer_id": SANDER}
    assert w.send("PUT", f"/placements/{old}", json=body).status_code == 409
    assert repo.who(SKIFTLEDER_SLOT) == [SIGRID]
    assert w.send("PUT", f"/placements/{old}", json={**body, "confirmed": True}).status_code == 200
    assert repo.who(SKIFTLEDER_SLOT) == [SANDER]
    assert audit[-1]["action"] == "shift.replace.confirmed"


def test_move_puts_the_person_on_an_empty_place_on_another_shift(writer):
    w, repo = writer
    assignment = repo.place(SKJENKER_SLOT, SANDER)
    response = w.send("POST", f"/placements/{assignment}/move", json={"slot_id": SECOND_SHIFT_SLOT})
    assert response.status_code == 200
    assert repo.who(SKJENKER_SLOT) == [] and repo.who(SECOND_SHIFT_SLOT) == [SANDER]


def test_remove_takes_the_person_off_and_checks_the_place(writer):
    w, repo = writer
    assignment = repo.place(SKJENKER_SLOT, SANDER)
    wrong_place = w.send("DELETE", f"/placements/{assignment}", params={"slot_id": SKIFTLEDER_SLOT})
    assert wrong_place.status_code == 409 and repo.who(SKJENKER_SLOT) == [SANDER]
    ok = w.send("DELETE", f"/placements/{assignment}", params={"slot_id": SKJENKER_SLOT})
    assert ok.status_code == 200 and repo.who(SKJENKER_SLOT) == []


# ---- swap ---------------------------------------------------------------------------------------


def test_swap_moves_both_people_and_keeps_their_assignment_ids(writer):
    w, repo = writer
    first = repo.place(SKJENKER_SLOT, SANDER)
    second = repo.place(SECOND_SHIFT_SLOT, UNDINE)
    response = w.send("POST", "/placements/swap", json={"assignment_a": first, "assignment_b": second})

    assert response.status_code == 200
    assert "har byttet plass" in response.json()["message"]
    assert repo.who(SKJENKER_SLOT) == [UNDINE] and repo.who(SECOND_SHIFT_SLOT) == [SANDER]
    assert repo.assignments[first].slot_id == SECOND_SHIFT_SLOT
    assert repo.assignments[second].slot_id == SKJENKER_SLOT


def test_swap_is_all_or_nothing(writer):
    w, repo = writer
    leader = repo.place(SKIFTLEDER_SLOT, SIGRID)
    crew = repo.place(SKJENKER_SLOT, SANDER)
    body = {"assignment_a": leader, "assignment_b": crew}

    refused = w.send("POST", "/placements/swap", json=body)
    # Sander is not an approved skiftleder, so he cannot take the leader place. Nobody moved.
    assert refused.status_code == 409 and refused.json()["detail"].startswith("Sander Skjenker:")
    assert repo.who(SKIFTLEDER_SLOT) == [SIGRID] and repo.who(SKJENKER_SLOT) == [SANDER]
    assert not [event for event in repo.events if event[0] == "move"]

    # Confirmed: the check is skipped for the qualification only, and both move.
    repo.events.clear()
    accepted = w.send("POST", "/placements/swap", json={**body, "confirmed": True})
    assert accepted.status_code == 200
    assert repo.who(SKIFTLEDER_SLOT) == [SANDER] and repo.who(SKJENKER_SLOT) == [SIGRID]


def test_swap_with_oneself_or_a_missing_assignment_is_harmless(writer):
    w, repo = writer
    assignment = repo.place(SKJENKER_SLOT, SANDER)
    same = w.send("POST", "/placements/swap", json={"assignment_a": assignment, "assignment_b": assignment})
    assert same.status_code == 200 and same.json()["message"] == "Ingen endring."
    gone = w.send("POST", "/placements/swap", json={"assignment_a": assignment, "assignment_b": 99999})
    assert gone.status_code == 409
    assert repo.who(SKJENKER_SLOT) == [SANDER]


def test_a_swap_cannot_reach_a_bar_outside_the_scope(monkeypatch, audit):
    repo = PlacingRepository()
    repo.admin_group_ids = [8]
    first = repo.place(SKJENKER_SLOT, SANDER)
    second = repo.place(SECOND_SHIFT_SLOT, UNDINE)
    client, repo, token = make(monkeypatch, UserRole.GROUP_ADMIN, repo)
    w = Writer(client, token)
    response = w.send("POST", "/placements/swap", json={"assignment_a": first, "assignment_b": second}, **{"If-Match": "x"})
    assert response.status_code == 403
    assert repo.who(SKJENKER_SLOT) == [SANDER]


# ---- week status ----------------------------------------------------------------------------------


def test_publish_a_coming_week_and_reopen_it(writer):
    w, repo = writer
    published = w.client.put(
        f"{BASE}/weeks/2026-11-09/status", params={"group_id": 7},
        json={"status": "published"},
        headers={**w.headers(), "If-Match": w.client.get(f"{BASE}/weeks/2026-11-09", params={"group_id": 7}).json()["version"]},
    )
    assert published.status_code == 200
    assert published.json()["board"]["status"] == "published"
    assert published.json()["message"] == "Uke 46 for Stjerne: publisert. Folk ser skiftene sine i appen nå."
    assert repo.statuses[(7, date(2026, 11, 9))] == "published"


def test_a_week_that_has_started_cannot_be_reopened(writer):
    w, repo = writer
    response = w.client.put(
        f"{BASE}/weeks/{WEEK}/status", params={"group_id": 7}, json={"status": "open"}, headers=w.headers()
    )
    assert response.status_code == 409
    assert "kan ikke åpnes" in response.json()["detail"]


# ---- plus and minus on the number of places ------------------------------------------------------


def _counts(repo: PlacingRepository, shift_id: int = 1) -> dict[str, int]:
    return {slot.kind: slot.count for slot in repo.shifts[shift_id].slots}


def _boxes(board: dict, shift_id: int = 1, kind: str | None = None) -> int:
    shift = next(s for s in board["shifts"] if s["shift_id"] == shift_id)
    return len([p for p in shift["places"] if kind is None or p["kind"] == kind])


def test_plus_adds_a_seat_to_the_slot_of_that_kind(writer, audit):
    w, repo = writer
    response = w.send("POST", "/shifts/1/places", json={"kind": "skjenker"})
    assert response.status_code == 200
    assert _counts(repo) == {"skiftleder": 1, "skjenker": 3}
    assert "Ny plass som skjenker" in response.json()["message"]
    assert _boxes(response.json()["board"], kind="skjenker") == 3
    assert audit[-1]["action"] == "shift.place.add" and audit[-1]["details"] == {"kind": "skjenker", "via": "leader-app"}


def test_plus_for_a_kind_the_shift_lacks_makes_a_new_slot(writer):
    w, repo = writer
    response = w.send("POST", "/shifts/1/places", json={"kind": "kokk"})
    assert response.status_code == 200
    assert _counts(repo) == {"skiftleder": 1, "skjenker": 2, "kokk": 1}
    assert [s.sort_order for s in repo.shifts[1].slots] == [0, 1, 2]
    assert _boxes(response.json()["board"], kind="kokk") == 1


def test_plus_refuses_an_unknown_kind_a_closed_shift_and_more_than_twenty(writer):
    w, repo = writer
    for kind in ("dj", "garderobe", "bartender"):
        assert w.send("POST", "/shifts/1/places", json={"kind": kind}).status_code == 409
    repo.shifts[1].is_closed = True
    closed = w.send("POST", "/shifts/1/places", json={"kind": "skjenker"})
    assert closed.status_code == 409 and closed.json()["detail"] == "Skiftet er stengt."
    repo.shifts[1].is_closed = False
    next(s for s in repo.shifts[1].slots if s.kind == "skjenker").count = 20
    full = w.send("POST", "/shifts/1/places", json={"kind": "skjenker"})
    assert full.status_code == 409 and "20" in full.json()["detail"]
    assert _counts(repo)["skjenker"] == 20


def test_minus_removes_one_empty_seat_and_deletes_a_slot_at_zero(writer, audit):
    w, repo = writer
    assert w.send("DELETE", f"/slots/{SKJENKER_SLOT}/seat").status_code == 200
    assert _counts(repo) == {"skiftleder": 1, "skjenker": 1}
    assert audit[-1]["action"] == "shift.place.remove"
    response = w.send("DELETE", f"/slots/{SKJENKER_SLOT}/seat")
    assert response.status_code == 200
    assert _counts(repo) == {"skiftleder": 1}
    assert _boxes(response.json()["board"], kind="skjenker") == 0


def test_minus_never_takes_a_person_off(writer):
    w, repo = writer
    repo.place(SKJENKER_SLOT, SANDER)
    repo.place(SKJENKER_SLOT, UNDINE)  # both seats taken
    response = w.send("DELETE", f"/slots/{SKJENKER_SLOT}/seat")
    assert response.status_code == 409
    assert "i bruk" in response.json()["detail"]
    assert repo.who(SKJENKER_SLOT) == [SANDER, UNDINE] and _counts(repo)["skjenker"] == 2
    next(s for s in repo.shifts[1].slots if s.kind == "skjenker").count = 3  # one empty seat again
    assert w.send("DELETE", f"/slots/{SKJENKER_SLOT}/seat").status_code == 200
    assert repo.who(SKJENKER_SLOT) == [SANDER, UNDINE] and _counts(repo)["skjenker"] == 2
    assert w.send("DELETE", f"/slots/{SKJENKER_SLOT}/seat").status_code == 409


def test_skiftleder_is_never_added_or_removed_with_plus_and_minus(writer):
    w, repo = writer
    added = w.send("POST", "/shifts/1/places", json={"kind": "skiftleder"})
    assert added.status_code == 409 and added.json()["detail"].startswith("Et skift har én skiftleder")
    removed = w.send("DELETE", f"/slots/{SKIFTLEDER_SLOT}/seat")  # empty, still refused
    assert removed.status_code == 409 and removed.json()["detail"].startswith("Et skift har én skiftleder")
    assert _counts(repo)["skiftleder"] == 1


def test_opplaering_is_not_a_kind_to_add(writer):
    w, repo = writer
    response = w.send("POST", "/shifts/1/places", json={"kind": "opplaering"})
    assert response.status_code == 409 and "skjenker" in response.json()["detail"]
    assert "opplaering" not in _counts(repo)


def test_minus_on_a_closed_shift_is_refused(writer):
    w, repo = writer
    repo.shifts[1].is_closed = True
    assert w.send("DELETE", f"/slots/{SKJENKER_SLOT}/seat").status_code == 409
    assert _counts(repo)["skjenker"] == 2


def test_plus_then_minus_gives_the_same_board(writer):
    w, repo = writer
    before = w.version()
    assert w.send("POST", "/shifts/1/places", json={"kind": "skjenker"}).status_code == 200
    assert w.version() != before
    assert w.send("DELETE", f"/slots/{SKJENKER_SLOT}/seat").status_code == 200
    assert w.version() == before


def test_plus_and_minus_need_the_safety_layer(writer):
    w, repo = writer
    assert w.send("POST", "/shifts/1/places", json={"kind": "skjenker"}, Origin="https://evil.example").status_code == 403
    assert w.send("DELETE", f"/slots/{SKJENKER_SLOT}/seat", **{"X-CSRF-Token": "forged"}).status_code == 403
    assert w.send("POST", "/shifts/1/places", json={"kind": "skjenker"}, **{"If-Match": "old"}).status_code == 409
    assert _counts(repo) == {"skiftleder": 1, "skjenker": 2}


def test_plus_and_minus_cannot_reach_a_bar_outside_the_scope(monkeypatch, audit):
    repo = PlacingRepository()
    repo.admin_group_ids = [8]  # the kitchen only
    client, repo, token = make(monkeypatch, UserRole.GROUP_ADMIN, repo)
    w = Writer(client, token)
    assert w.send("POST", "/shifts/1/places", json={"kind": "skjenker"}, **{"If-Match": "x"}).status_code == 403
    assert w.send("DELETE", f"/slots/{SKJENKER_SLOT}/seat", **{"If-Match": "x"}).status_code == 403
    assert _counts(repo) == {"skiftleder": 1, "skjenker": 2}


# ---- a change must land in the week the leader is looking at ------------------------------------


def test_a_write_that_does_not_change_this_week_is_undone(writer, monkeypatch):
    """The version check uses the week in the query; the ids in the body could belong to another bar.
    If the write leaves this week's version unchanged, it went somewhere else: 409 and a rollback.

    The request session commits on any normal response and a 4xx from an endpoint is one, so the
    explicit rollback is what undoes the change in a real database (the in-memory repository here
    cannot roll back, so the test checks that it is asked to)."""
    w, repo = writer
    rolled_back: list[bool] = []

    async def fake_rollback() -> None:
        rolled_back.append(True)

    async def lands_elsewhere(self, **kwargs):  # the service "succeeds", this week stays the same
        return "Satt inn."

    monkeypatch.setattr("app.api.v1.leader_shifts.rollback_request_session", fake_rollback)
    monkeypatch.setattr(ShiftsService, "assign", lands_elsewhere)
    response = w.send("POST", "/placements", json={"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER})
    assert response.status_code == 409
    assert "ikke lagret" in response.json()["detail"]
    assert rolled_back == [True]


def test_a_normal_write_is_not_rolled_back(writer, monkeypatch):
    w, repo = writer
    rolled_back: list[bool] = []

    async def fake_rollback() -> None:
        rolled_back.append(True)

    monkeypatch.setattr("app.api.v1.leader_shifts.rollback_request_session", fake_rollback)
    response = w.send("POST", "/placements", json={"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER})
    assert response.status_code == 200
    assert repo.who(SKJENKER_SLOT) == [SANDER]
    assert rolled_back == []


def test_publishing_again_is_not_treated_as_a_misplaced_write(writer, monkeypatch):
    """A status write can legitimately change nothing (two leaders publish at once)."""
    w, repo = writer
    rolled_back: list[bool] = []

    async def fake_rollback() -> None:
        rolled_back.append(True)

    monkeypatch.setattr("app.api.v1.leader_shifts.rollback_request_session", fake_rollback)
    url = f"{BASE}/weeks/2026-11-09/status"
    version = lambda: w.client.get(f"{BASE}/weeks/2026-11-09", params={"group_id": 7}).json()["version"]  # noqa: E731
    first = w.client.put(url, params={"group_id": 7}, json={"status": "published"}, headers={**w.headers(), "If-Match": version()})
    assert first.status_code == 200
    again = w.client.put(url, params={"group_id": 7}, json={"status": "published"}, headers={**w.headers(), "If-Match": version()})
    assert again.status_code == 200
    assert rolled_back == []
