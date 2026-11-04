"""``POST .../suggestions`` ("Lag forslag for uka"): the real rules decide who may take a place, the
engine proposes from the wishes, nothing is placed. The real ``ShiftsService`` on the in-memory
repository of ``tests/support/week_placing.py``; week of Monday 2026-11-02, made-up people.

Shift 1 (Dagskift, 17:00-23:00 Oslo): a skiftleder place and two skjenker places. Shift 2
(Kveldsskift, 20:00-00:00): two skjenker places. The two overlap in time."""

from __future__ import annotations

import pytest

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

SHIFT_1, SHIFT_2 = 1, 2


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.leader_shifts.log_admin_activity", lambda **kwargs: None
    )
    client, repo, token = make(monkeypatch)
    return Writer(client, token), repo


def generate(w: Writer, **headers):
    return w.send("POST", f"/weeks/{WEEK}/suggestions", params={}, **headers)


def picks(repo: PlacingRepository) -> dict[int, list[int]]:
    found: dict[int, list[int]] = {}
    for record in repo.suggestions:
        found.setdefault(record.slot_id, []).append(record.volunteer_id)
    return found


def test_suggestions_follow_the_wishes_and_the_rules(writer) -> None:
    w, repo = writer
    repo.wishes.update(
        {
            (SHIFT_1, SIGRID): "vil",
            (SHIFT_1, SANDER): "vil",
            (SHIFT_1, HALLE): "kan",
            (SHIFT_1, TORA): "vil",
        }
    )

    response = generate(w)

    assert response.status_code == 200, response.text
    found = picks(repo)
    assert found[SKIFTLEDER_SLOT] == [SIGRID]
    assert sorted(found[SKJENKER_SLOT]) == sorted(
        [SANDER, HALLE]
    )  # Tora is a trainee: not allowed
    assert TORA not in [v for people in found.values() for v in people]
    assert response.json()["message"] == "Laget 3 forslag."
    # Nothing is placed: a suggestion is not an assignment.
    assert repo.who(SKIFTLEDER_SLOT) == [] and repo.who(SKJENKER_SLOT) == []


def test_nobody_is_suggested_for_a_shift_they_did_not_offer_for(writer) -> None:
    w, repo = writer
    repo.wishes.update(
        {(SHIFT_1, SANDER): "nei", (SHIFT_1, HALLE): "kan"}
    )  # Undine and Sigrid: no answer

    generate(w)

    assert picks(repo) == {SKJENKER_SLOT: [HALLE]}


def test_the_board_shows_the_suggestions_with_their_reason(writer) -> None:
    w, repo = writer
    repo.wishes[(SHIFT_1, SIGRID)] = "vil"

    body = generate(w).json()

    leader = next(
        p
        for s in body["board"]["shifts"]
        for p in s["places"]
        if p["slot_id"] == SKIFTLEDER_SLOT
    )
    assert leader["suggestion"]["volunteer_id"] == SIGRID
    assert leader["suggestion"]["why"].startswith(
        "Valgt fordi Sigrid vil jobbe dette skiftet"
    )
    assert leader["assignment"] is None


def test_one_person_is_not_suggested_for_two_overlapping_shifts(writer) -> None:
    w, repo = writer
    repo.wishes.update(
        {(SHIFT_1, SANDER): "vil", (SHIFT_2, SANDER): "vil", (SHIFT_2, UNDINE): "kan"}
    )

    generate(w)

    found = picks(repo)
    assert SANDER in found[SKJENKER_SLOT]
    assert found[SECOND_SHIFT_SLOT] == [
        UNDINE
    ]  # Sander is already taken for the overlapping shift


def test_someone_already_placed_on_an_overlapping_shift_is_left_out(writer) -> None:
    w, repo = writer
    repo.place(SKJENKER_SLOT, SANDER)  # a leader placed Sander on shift 1
    repo.wishes.update({(SHIFT_2, SANDER): "vil", (SHIFT_2, UNDINE): "kan"})

    generate(w)

    assert picks(repo) == {SECOND_SHIFT_SLOT: [UNDINE]}
    assert repo.who(SKJENKER_SLOT) == [SANDER]  # untouched


def test_places_already_filled_get_no_suggestion(writer) -> None:
    w, repo = writer
    repo.place(SKIFTLEDER_SLOT, SIGRID)
    repo.wishes.update({(SHIFT_1, SIGRID): "vil", (SHIFT_1, HALLE): "vil"})

    generate(w)

    assert SKIFTLEDER_SLOT not in picks(repo)


def test_generating_again_replaces_the_pending_suggestions(writer) -> None:
    w, repo = writer
    repo.wishes[(SHIFT_1, SANDER)] = "vil"
    generate(w)
    repo.wishes[(SHIFT_1, SANDER)] = "nei"
    repo.wishes[(SHIFT_1, HALLE)] = "vil"

    generate(w)

    assert picks(repo) == {SKJENKER_SLOT: [HALLE]}


def test_without_any_offers_nothing_is_suggested(writer) -> None:
    w, repo = writer
    response = generate(w)
    assert response.status_code == 200
    assert (
        response.json()["message"].startswith("Ingen endring.")
        and repo.suggestions == []
    )


def test_people_who_offered_but_got_nothing_and_unfilled_places_are_reported(
    writer,
) -> None:
    w, repo = writer
    repo.wishes.update(
        {(SHIFT_1, SANDER): "vil", (SHIFT_2, SANDER): "vil"}
    )  # one shift only, they overlap

    body = generate(w).json()

    texts = [n["text"] for n in body["warnings"]]
    assert any("plasser fikk ikke noe forslag" in t for t in texts)


def test_a_published_week_is_not_marked_changed_and_nothing_is_visible(writer) -> None:
    w, repo = writer
    repo.statuses[(7, __import__("datetime").date(2026, 11, 2))] = "published"
    repo.wishes[(SHIFT_1, SIGRID)] = "vil"

    assert generate(w).status_code == 200
    assert repo.changes == {}
    assert repo.who(SKIFTLEDER_SLOT) == []


def test_it_needs_the_safety_layer_and_the_current_version(writer) -> None:
    w, repo = writer
    repo.wishes[(SHIFT_1, SIGRID)] = "vil"

    assert generate(w, Origin="https://elsewhere.example").status_code == 403
    assert generate(w, **{"X-CSRF-Token": None}).status_code == 403
    assert generate(w, **{"If-Match": "old"}).status_code == 409
    assert repo.suggestions == []


def test_the_same_week_gives_the_same_suggestions_every_time(writer) -> None:
    w, repo = writer
    repo.wishes.update(
        {
            (SHIFT_1, SIGRID): "vil",
            (SHIFT_1, SANDER): "kan",
            (SHIFT_1, HALLE): "kan",
            (SHIFT_2, UNDINE): "vil",
            (SHIFT_2, HALLE): "kan",
        }
    )
    generate(w)
    first = [(r.slot_id, r.volunteer_id, r.why) for r in repo.suggestions]

    generate(w)

    assert [(r.slot_id, r.volunteer_id, r.why) for r in repo.suggestions] == first
