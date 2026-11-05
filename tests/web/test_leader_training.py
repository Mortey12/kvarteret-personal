"""Training kept on the person: shown on the board wherever the person stands, counted from
finished shifts, approved or extended by a leader, and a warning when a skiftleder in training
has no experienced skiftleder with them. The real ``ShiftsService`` on the in-memory repository."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.support.week_placing import (
    SIGRID,
    SKIFTLEDER_SLOT,
    SKJENKER_SLOT,
    SANDER,
    TORA,
    UNDINE,
)
from tests.web.test_leader_shifts_writes import WEEK, Writer, make

BASE = "/api/v1/leader/shifts"


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr("app.api.v1.leader_shifts.log_admin_activity", lambda **kwargs: None)
    client, repo, token = make(monkeypatch)
    return Writer(client, token), repo


def board(w: Writer) -> dict:
    return w.client.get(f"{BASE}/weeks/{WEEK}", params={"group_id": 7}).json()


def person(data: dict, volunteer_id: int) -> dict:
    return next(p for p in data["people"] if p["volunteer_id"] == volunteer_id)


def act(w: Writer, what: str, volunteer_id: int, qualification: str = "skjenker", **extra):
    return w.send("POST", f"/weeks/{WEEK}/training/{what}", json={"volunteer_id": volunteer_id, "qualification": qualification, **extra})


def place_on(w: Writer, slot: int, volunteer_id: int):
    return w.send("POST", "/placements", json={"slot_id": slot, "volunteer_id": volunteer_id, "confirmed": True})


def test_a_person_in_training_is_marked_on_the_person_not_on_a_place(writer) -> None:
    w, repo = writer
    # Tora is in training for skjenker in the made-up data; Sander is not.
    data = board(w)
    assert person(data, SANDER)["training"] == []
    tora = person(data, TORA)
    assert [(t["qualification"], t["done"], t["required"], t["ready"]) for t in tora["training"]] == [("skjenker", 0, 3, False)]
    assert tora["tags"] == ["opplæring 0/3"]


def test_a_leader_puts_someone_in_training(writer) -> None:
    w, repo = writer
    assert act(w, "start", SANDER, "kokk").status_code == 200
    assert [t["qualification"] for t in person(board(w), SANDER)["training"]] == ["kokk"]
    # Starting the same training twice is refused, and so is training someone already approved.
    assert act(w, "start", SANDER, "kokk").status_code == 409
    assert act(w, "start", SANDER, "skjenker").status_code == 409


def test_the_place_says_which_training_shift_it_is_and_it_follows_the_person(writer) -> None:
    w, repo = writer
    place_on(w, SKJENKER_SLOT, TORA)

    def tora_places():
        return [p for s in board(w)["shifts"] for p in s["places"] if p["assignment"] and p["assignment"]["volunteer_id"] == TORA]

    assert [p["assignment"]["training"] for p in tora_places()] == ["opplæring 1/3"]
    # Removing her and placing her again changes nothing about her training: it is on the person.
    w.send("DELETE", f"/placements/{tora_places()[0]['assignment']['assignment_id']}")
    place_on(w, SKJENKER_SLOT, TORA)
    assert [p["assignment"]["training"] for p in tora_places()] == ["opplæring 1/3"]
    assert person(board(w), TORA)["training"][0]["qualification"] == "skjenker"


def make_done(repo, target: int = 1) -> None:
    """Tora has worked a shift that is over since her training started."""
    for shift in repo.shifts.values():
        shift.starts_at = datetime(2026, 10, 20, 16, tzinfo=UTC)
        shift.ends_at = datetime(2026, 10, 20, 22, tzinfo=UTC)
    repo.training_targets[(TORA, "skjenker", 7)] = target


def test_ready_after_the_required_shifts_then_the_leader_approves(writer) -> None:
    w, repo = writer
    place_on(w, SKJENKER_SLOT, TORA)
    make_done(repo, target=1)

    assert person(board(w), TORA)["training"][0]["ready"] is True

    assert act(w, "approve", TORA).status_code == 200
    assert person(board(w), TORA)["training"] == []
    assert (TORA, "skjenker", "approved", 7) in repo.qualifications


def test_the_board_names_the_courses_still_missing(writer) -> None:
    w, repo = writer
    assert person(board(w), TORA)["training"][0]["courses_pending"] == ["Skjenkekurs"]
    repo.completed_courses[TORA] = {41}
    assert person(board(w), TORA)["training"][0]["courses_pending"] == []


def test_only_the_course_of_the_qualification_is_ever_registered(writer) -> None:
    w, repo = writer
    place_on(w, SKJENKER_SLOT, TORA)
    make_done(repo, target=1)
    repo.courses_by_name["Brannkurs"] = 43  # for everyone: must stay out of it

    act(w, "approve", TORA)

    assert repo.completed_courses == {TORA: {41}}


def test_a_qualification_whose_course_does_not_exist_is_approved_without_one(writer) -> None:
    w, repo = writer
    act(w, "start", SANDER, "kokk")  # no "Kokkekurs" in the made-up data
    for shift in repo.shifts.values():
        shift.starts_at = datetime(2026, 10, 20, 16, tzinfo=UTC)
        shift.ends_at = datetime(2026, 10, 20, 22, tzinfo=UTC)
    repo.training_targets[(SANDER, "kokk", 7)] = 1
    place_on(w, SKJENKER_SLOT, SANDER)

    response = act(w, "approve", SANDER, "kokk")

    assert response.status_code == 200
    assert "Kurs registrert" not in response.json()["message"]
    assert SANDER not in repo.completed_courses


def test_approving_also_registers_the_required_course(writer) -> None:
    w, repo = writer
    place_on(w, SKJENKER_SLOT, TORA)
    make_done(repo, target=1)

    response = act(w, "approve", TORA)

    assert response.status_code == 200
    assert "Skjenkekurs" in response.json()["message"]
    assert ("course", TORA, 41, 20262) in repo.events


def test_approving_can_leave_the_course_alone(writer) -> None:
    w, repo = writer
    place_on(w, SKJENKER_SLOT, TORA)
    make_done(repo, target=1)

    assert act(w, "approve", TORA, register_courses=False).status_code == 200
    assert not [event for event in repo.events if event[0] == "course"]
    assert TORA not in repo.completed_courses


def test_or_gives_one_more_shift(writer) -> None:
    w, repo = writer
    place_on(w, SKJENKER_SLOT, TORA)
    make_done(repo, target=1)

    assert act(w, "extend", TORA).status_code == 200

    training = person(board(w), TORA)["training"][0]
    assert (training["done"], training["required"], training["ready"]) == (1, 2, False)


def test_approve_and_extend_are_refused_before_the_shifts_are_done(writer) -> None:
    w, repo = writer
    approve = act(w, "approve", TORA)
    assert approve.status_code == 409 and "Vent til opplæringen er ferdig" in approve.json()["detail"]
    assert act(w, "extend", TORA).status_code == 409


def test_stopping_takes_the_person_out_of_training(writer) -> None:
    w, repo = writer
    assert act(w, "stop", TORA).status_code == 200
    assert person(board(w), TORA)["training"] == []
    assert act(w, "stop", TORA).status_code == 409


def test_a_skiftleder_in_training_with_no_experienced_skiftleder_gets_a_yellow_warning(writer) -> None:
    w, repo = writer
    act(w, "start", UNDINE, "skiftleder")

    data = board(w)
    fit = data["fit"][str(UNDINE)][str(SKJENKER_SLOT)]
    assert any(note["level"] == "yellow" and "erfaren skiftleder" in note["text"] for note in fit["warnings"])

    # With Sigrid (experienced) on the shift's skiftleder place the warning is gone.
    place_on(w, SKIFTLEDER_SLOT, SIGRID)
    fit = board(w)["fit"][str(UNDINE)][str(SKJENKER_SLOT)]
    assert not any("erfaren skiftleder" in note["text"] for note in fit["warnings"])


def test_nobody_outside_the_scope_can_start_training(writer) -> None:
    w, repo = writer
    response = w.send("POST", f"/weeks/{WEEK}/training/start", json={"volunteer_id": 99999, "qualification": "skjenker"})
    assert response.status_code in (404, 409)
