"""'Changed after publishing': a leader's change to an explicitly published week flags it with
the leader's name and the time; publishing again clears it. Volunteers' status stays "published".
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.support.week_placing import SANDER, SKJENKER_SLOT
from tests.web.test_leader_shifts_writes import BASE, WEEK, Writer, make

MONDAY = date(2026, 11, 2)


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr("app.api.v1.leader_shifts.log_admin_activity", lambda **kwargs: None)
    client, repo, token = make(monkeypatch)
    return Writer(client, token), repo


def _board(w):
    return w.client.get(f"{BASE}/weeks/{WEEK}", params={"group_id": 7}).json()


def _place(w):
    return w.send("POST", "/placements", json={"slot_id": SKJENKER_SLOT, "volunteer_id": SANDER})


def test_a_change_to_a_published_week_flags_it_with_name_and_time(writer):
    w, repo = writer
    repo.statuses[(7, MONDAY)] = "published"
    assert _board(w)["status"] == "published" and _board(w)["last_changed"] is None

    response = _place(w)

    board = response.json()["board"]
    assert board["status"] == "changed_after_publish"
    assert board["last_changed"]["by"] and board["last_changed"]["at"].startswith("2026-")
    assert [item["status"] for item in board["strip"] if item["week_start"] == WEEK] == ["changed_after_publish"]
    # The stored status is untouched, so volunteers and the wish rules still see "published".
    assert repo.statuses[(7, MONDAY)] == "published"


def test_a_week_without_a_published_row_is_never_flagged(writer):
    w, repo = writer
    assert (7, MONDAY) not in repo.statuses
    assert _place(w).json()["board"]["status"] != "changed_after_publish"
    repo.statuses[(7, MONDAY)] = "open"
    assert _board(w)["status"] == "open" and _board(w)["last_changed"] is None


def test_a_refused_write_does_not_flag(writer):
    w, repo = writer
    repo.statuses[(7, MONDAY)] = "published"
    response = w.send("POST", "/placements", json={"slot_id": SKJENKER_SLOT, "volunteer_id": 999999})
    assert response.status_code >= 400
    assert _board(w)["status"] == "published"


def test_adding_a_place_flags_the_week_too(writer):
    w, repo = writer
    repo.statuses[(7, MONDAY)] = "published"
    shift_id = _board(w)["shifts"][0]["shift_id"]
    assert w.send("POST", f"/shifts/{shift_id}/places", json={"kind": "skjenker"}).status_code == 200
    assert _board(w)["status"] == "changed_after_publish"


def test_publishing_again_clears_the_flag(writer):
    w, repo = writer
    repo.statuses[(7, MONDAY)] = "published"
    _place(w)
    assert _board(w)["status"] == "changed_after_publish"

    response = w.client.put(
        f"{BASE}/weeks/{WEEK}/status", params={"group_id": 7}, json={"status": "published"}, headers=w.headers()
    )

    assert response.status_code == 200
    assert response.json()["board"]["status"] == "published"
    assert response.json()["board"]["last_changed"] is None
    assert (7, MONDAY) not in repo.changes
