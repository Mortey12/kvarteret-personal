"""Pending suggestions on the leader board: shown on empty places, accepted or discarded, and a
week with suggestions left cannot be published. The real ``ShiftsService`` on the in-memory
repository of ``tests/support/week_placing.py``. Made-up people only. Week of Monday 2026-11-02."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.auth.roles import UserRole
from app.domain.shifts.errors import ShiftPlacementError
from app.domain.shifts.permissions import manageable_groups
from app.domain.shifts.service import SUGGESTIONS_PENDING, ShiftsService
from tests.support.week_placing import (
    HALLE,
    SANDER,
    SECOND_SHIFT_SLOT,
    SIGRID,
    SKIFTLEDER_SLOT,
    SKJENKER_SLOT,
    TORA,
    UNDINE,
    PlacingRepository,
)
from tests.web.test_leader_shifts_writes import WEEK, Writer, make

THIS_WEEK = date(2026, 11, 2)


def service(repo):
    return (
        ShiftsService(repo, now=lambda: datetime(2026, 11, 2, 12, tzinfo=UTC)),
        manageable_groups(UserRole.ADMIN, [], {}),
    )


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.leader_shifts.log_admin_activity", lambda **kwargs: None
    )
    client, repo, token = make(monkeypatch)
    return Writer(client, token), repo


def board(w: Writer) -> dict:
    return w.client.get(
        f"/api/v1/leader/shifts/weeks/{WEEK}", params={"group_id": 7}
    ).json()


def accept(w: Writer, **headers):
    return w.send("POST", f"/weeks/{WEEK}/suggestions/accept", params={}, **headers)


def discard(w: Writer, **headers):
    return w.send("POST", f"/weeks/{WEEK}/suggestions/discard", params={}, **headers)


def places(data: dict, slot_id: int) -> list[dict]:
    return [
        p
        for shift in data["shifts"]
        for p in shift["places"]
        if p["slot_id"] == slot_id
    ]


# ---- on the board --------------------------------------------------------------------------------


def test_a_suggestion_is_drawn_on_the_next_empty_place_with_its_reason(writer) -> None:
    w, repo = writer
    repo.suggest(SKJENKER_SLOT, SANDER, "Valgt fordi han vil")
    repo.suggest(SKJENKER_SLOT, HALLE, "Valgt fordi hun kan")

    seats = places(board(w), SKJENKER_SLOT)

    assert [p["suggestion"] for p in seats] == [
        {"volunteer_id": SANDER, "why": "Valgt fordi han vil"},
        {"volunteer_id": HALLE, "why": "Valgt fordi hun kan"},
    ]
    assert all(
        p["assignment"] is None for p in seats
    )  # nobody is placed by a suggestion


def test_a_suggestion_never_covers_a_person_already_there(writer) -> None:
    w, repo = writer
    repo.place(SKJENKER_SLOT, TORA)
    repo.suggest(SKJENKER_SLOT, SANDER)

    seats = places(board(w), SKJENKER_SLOT)

    assert (
        seats[0]["assignment"]["volunteer_id"] == TORA
        and seats[0]["suggestion"] is None
    )
    assert seats[1]["suggestion"]["volunteer_id"] == SANDER


def test_suggestions_change_the_version_so_a_stale_copy_gets_409(writer) -> None:
    w, repo = writer
    stale = w.version()
    repo.suggest(SKJENKER_SLOT, SANDER)

    assert accept(w, **{"If-Match": stale}).status_code == 409
    assert discard(w, **{"If-Match": stale}).status_code == 409
    assert repo.who(SKJENKER_SLOT) == []


# ---- accept --------------------------------------------------------------------------------------


def test_accept_places_the_suggested_people_and_clears_the_suggestions(writer) -> None:
    w, repo = writer
    repo.suggest(SKIFTLEDER_SLOT, SIGRID)
    repo.suggest(SKJENKER_SLOT, SANDER)
    repo.suggest(SECOND_SHIFT_SLOT, UNDINE)

    response = accept(w)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"] == "Godtok 3 forslag."
    assert body["warnings"] == []
    assert repo.who(SKIFTLEDER_SLOT) == [SIGRID]
    assert repo.who(SKJENKER_SLOT) == [SANDER]
    assert repo.suggestions == []
    assert all(
        p["suggestion"] is None for s in body["board"]["shifts"] for p in s["places"]
    )


def test_accept_goes_through_the_rules_and_lists_what_it_skipped(writer) -> None:
    w, repo = writer
    repo.suggest(SKJENKER_SLOT, TORA)  # a trainee lacks what the place needs
    repo.suggest(SKJENKER_SLOT, SANDER)
    repo.place(SECOND_SHIFT_SLOT, UNDINE)
    repo.place(SECOND_SHIFT_SLOT, HALLE)
    repo.suggest(SECOND_SHIFT_SLOT, SANDER)  # the place was filled meanwhile

    response = accept(w)

    body = response.json()
    assert response.status_code == 200
    assert body["message"] == "Godtok 1 forslag. 2 ble ikke godtatt."
    texts = [n["text"] for n in body["warnings"]]
    assert any(t.startswith("Tora Trainee") for t in texts)
    assert any("plassen er tatt" in t for t in texts)
    assert repo.who(SKJENKER_SLOT) == [SANDER]
    assert repo.suggestions == []  # none is left to block publishing


def test_accept_with_nothing_pending_changes_nothing(writer) -> None:
    w, _ = writer
    response = accept(w)
    assert response.status_code == 200 and response.json()["message"].startswith(
        "Ingen endring."
    )


def test_accept_marks_a_published_week_changed_but_a_discard_does_not(writer) -> None:
    w, repo = writer
    repo.statuses[(7, THIS_WEEK)] = "published"
    repo.suggest(SKIFTLEDER_SLOT, SIGRID)
    assert discard(w).status_code == 200
    assert repo.changes == {}  # nobody was placed

    repo.suggest(SKIFTLEDER_SLOT, SIGRID)
    assert accept(w).status_code == 200
    assert (7, THIS_WEEK) in repo.changes


def test_a_failure_while_accepting_is_rolled_back(writer, monkeypatch) -> None:
    w, repo = writer
    repo.suggest(SKIFTLEDER_SLOT, SIGRID)
    repo.suggest(SKJENKER_SLOT, SANDER)
    rolled_back: list[bool] = []

    async def fake_rollback() -> None:
        rolled_back.append(True)

    calls = {"n": 0}
    real_add = repo.add_assignment

    async def failing_add(slot_id, volunteer_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("database went away")
        return await real_add(slot_id, volunteer_id)

    monkeypatch.setattr(
        "app.api.v1.leader_shifts.rollback_request_session", fake_rollback
    )
    monkeypatch.setattr(repo, "add_assignment", failing_add)

    with pytest.raises(RuntimeError):
        accept(w)

    assert rolled_back == [True]


# ---- discard -------------------------------------------------------------------------------------


def test_discard_removes_the_suggestions_and_places_nobody(writer) -> None:
    w, repo = writer
    repo.suggest(SKIFTLEDER_SLOT, SIGRID)
    repo.suggest(SKJENKER_SLOT, SANDER)

    response = discard(w)

    assert response.status_code == 200
    assert response.json()["message"] == "Forkastet 2 forslag."
    assert repo.suggestions == [] and repo.who(SKIFTLEDER_SLOT) == []


def test_discard_with_nothing_pending_changes_nothing(writer) -> None:
    w, _ = writer
    assert discard(w).json()["message"].startswith("Ingen endring.")


# ---- the safety layer ----------------------------------------------------------------------------


@pytest.mark.parametrize("action", [accept, discard])
def test_both_need_the_safety_layer(writer, action) -> None:
    w, repo = writer
    repo.suggest(SKIFTLEDER_SLOT, SIGRID)

    assert action(w, Origin="https://elsewhere.example").status_code == 403
    assert action(w, **{"X-CSRF-Token": None}).status_code == 403
    assert action(w, **{"If-Match": None}).status_code in (400, 422)
    assert len(repo.suggestions) == 1 and repo.who(SKIFTLEDER_SLOT) == []


# ---- publishing ----------------------------------------------------------------------------------


def test_a_week_with_suggestions_cannot_be_published_until_they_are_dealt_with(
    writer,
) -> None:
    w, repo = writer
    repo.suggest(SKIFTLEDER_SLOT, SIGRID)

    refused = w.send(
        "PUT", f"/weeks/{WEEK}/status", json={"status": "published"}, params={}, **{}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == SUGGESTIONS_PENDING

    assert discard(w).status_code == 200
    published = w.send(
        "PUT", f"/weeks/{WEEK}/status", json={"status": "published"}, params={}
    )
    assert published.status_code == 200


# ---- saving (what an engine calls) ---------------------------------------------------------------


async def test_save_replaces_the_old_suggestions_and_drops_duplicates() -> None:
    repo = PlacingRepository()
    svc, scope = service(repo)
    repo.suggest(SKIFTLEDER_SLOT, SIGRID, "gammelt")

    saved = await svc.save_suggestions(
        group_id=7,
        week=THIS_WEEK,
        items=[
            (SKJENKER_SLOT, SANDER, "a"),
            (SKJENKER_SLOT, SANDER, "dobbelt"),
            (SKJENKER_SLOT, HALLE, "b"),
        ],
        scope=scope,
    )

    assert saved == 2
    assert [(r.slot_id, r.volunteer_id, r.why) for r in repo.suggestions] == [
        (SKJENKER_SLOT, SANDER, "a"),
        (SKJENKER_SLOT, HALLE, "b"),
    ]


async def test_save_refuses_a_place_outside_the_week() -> None:
    repo = PlacingRepository()
    svc, scope = service(repo)

    with pytest.raises(ShiftPlacementError):
        await svc.save_suggestions(
            group_id=7, week=THIS_WEEK, items=[(999, SANDER, "x")], scope=scope
        )
    assert repo.suggestions == []
