"""Copy last week's people onto this week's empty places (leader app, "Kopier fra forrige uke").

The real ``ShiftsService`` on the in-memory repository of ``tests/support/week_placing.py``. This
week is Monday 2026-11-02 (shifts on the Tuesday); last week is 2026-10-26 (shifts on 27 October,
same clock time in Oslo). Made-up people only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.auth.roles import UserRole
from app.domain.shifts.models import ShiftRecord, SlotRecord
from app.domain.shifts.permissions import manageable_groups
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.week_placing import copy_previous_week
from tests.support.week_placing import (
    HALLE,
    SANDER,
    SIGRID,
    SKIFTLEDER_SLOT,
    SKJENKER_SLOT,
    SECOND_SHIFT_SLOT,
    TORA,
    UNDINE,
    PlacingRepository,
)
from tests.web.test_leader_shifts_writes import WEEK, Writer, make

THIS_WEEK = date(2026, 11, 2)
LAST_DAY_SKIFTLEDER, LAST_DAY_SKJENKER, LAST_EVENING_SKJENKER = 30, 31, 40


def with_last_week(repo: PlacingRepository) -> PlacingRepository:
    """Last week's Dagskift (Sigrid, Sander, Halle) and Kveldsskift (Undine), same times as this week's."""
    start = datetime(2026, 10, 27, 16, 0, tzinfo=UTC)  # 17:00 Oslo, as on 3 November
    repo.shifts[3] = ShiftRecord(
        3,
        7,
        "Stjerne",
        "Stjernesalen",
        "Dagskift",
        start,
        start.replace(hour=22),
        None,
        False,
        False,
        [
            SlotRecord(LAST_DAY_SKIFTLEDER, "skiftleder", 1, False, 0, []),
            SlotRecord(LAST_DAY_SKJENKER, "skjenker", 2, False, 1, []),
        ],
    )
    repo.shifts[4] = ShiftRecord(
        4,
        7,
        "Stjerne",
        "Stjernesalen",
        "Kveldsskift",
        start.replace(hour=19),
        start.replace(hour=23),
        None,
        False,
        False,
        [
            SlotRecord(LAST_EVENING_SKJENKER, "skjenker", 2, False, 0, []),
        ],
    )
    repo.place(LAST_DAY_SKIFTLEDER, SIGRID)
    repo.place(LAST_DAY_SKJENKER, SANDER)
    repo.place(LAST_DAY_SKJENKER, HALLE)
    repo.place(LAST_EVENING_SKJENKER, UNDINE)
    return repo


def service(repo: PlacingRepository) -> tuple[ShiftsService, object]:
    return (
        ShiftsService(repo, now=lambda: datetime(2026, 11, 2, 12, tzinfo=UTC)),
        manageable_groups(UserRole.ADMIN, [], {}),
    )


async def run(repo: PlacingRepository):
    svc, scope = service(repo)
    return await copy_previous_week(svc, group_id=7, week=THIS_WEEK, scope=scope)


async def test_last_weeks_people_go_to_the_same_places(monkeypatch) -> None:
    repo = with_last_week(PlacingRepository())

    result = await run(repo)

    assert result.placed == 4
    assert repo.who(SKIFTLEDER_SLOT) == [SIGRID]
    assert sorted(repo.who(SKJENKER_SLOT)) == sorted([SANDER, HALLE])
    assert repo.who(SECOND_SHIFT_SLOT) == [UNDINE]
    assert result.message == "Kopierte 4 plasser fra uke 44." and result.skipped == []


async def test_only_empty_places_are_filled_and_nobody_is_removed() -> None:
    repo = with_last_week(PlacingRepository())
    repo.place(SKJENKER_SLOT, TORA)  # a different person already stands here
    repo.place(SECOND_SHIFT_SLOT, SIGRID)
    repo.place(SECOND_SHIFT_SLOT, SANDER)  # Kveldsskift is full

    result = await run(repo)

    assert TORA in repo.who(SKJENKER_SLOT)  # still there
    assert sorted(repo.who(SECOND_SHIFT_SLOT)) == sorted([SIGRID, SANDER])  # untouched
    assert len(repo.who(SKJENKER_SLOT)) == 2  # one empty seat, one placed
    assert result.placed == 2  # Sigrid (skiftleder) and one skjenker
    assert any("fikk ikke plass" in line for line in result.skipped)


async def test_someone_who_answered_kan_ikke_is_skipped_and_named() -> None:
    repo = with_last_week(PlacingRepository())
    repo.wishes[(1, HALLE)] = "nei"

    result = await run(repo)

    assert HALLE not in repo.who(SKJENKER_SLOT)
    assert repo.who(SKJENKER_SLOT) == [SANDER]
    assert result.skipped == [
        "Halle Hjelper: svarte «kan ikke» for dagskift tirsdag 3.11."
    ]
    assert "1 ble ikke kopiert" in result.message


async def test_a_rule_still_refuses_and_is_reported_not_forced() -> None:
    repo = with_last_week(PlacingRepository())
    repo.shifts[3].slots[
        1
    ].people = []  # last week's skjenkere: only a trainee, who lacks what the place needs
    repo.place(LAST_DAY_SKJENKER, TORA)

    result = await run(repo)

    assert TORA not in repo.who(SKJENKER_SLOT)
    assert any(line.startswith("Tora Trainee") for line in result.skipped)


async def test_someone_who_could_not_come_last_week_is_not_copied() -> None:
    repo = with_last_week(PlacingRepository())
    repo.place(LAST_DAY_SKJENKER, TORA, absent=True)  # said "kan ikke i dag" last week

    result = await run(repo)

    assert TORA not in repo.who(SKJENKER_SLOT)
    assert result.skipped == []


async def test_a_person_who_left_the_group_is_skipped() -> None:
    repo = with_last_week(PlacingRepository())
    repo.members = [m for m in repo.members if m.volunteer_id != HALLE]

    result = await run(repo)

    assert HALLE not in repo.who(SKJENKER_SLOT)
    assert "Halle Hjelper: er ikke lenger med i gruppa." in result.skipped


async def test_a_closed_shift_is_left_alone() -> None:
    repo = with_last_week(PlacingRepository())
    repo.shifts[1].is_closed = True

    result = await run(repo)

    assert repo.who(SKIFTLEDER_SLOT) == [] and repo.who(SKJENKER_SLOT) == []
    assert result.placed == 1  # only the Kveldsskift


async def test_no_shifts_last_week_changes_nothing() -> None:
    result = await run(PlacingRepository())
    assert result.placed == 0 and result.message.startswith("Ingen endring.")


async def test_copying_twice_adds_nothing_the_second_time() -> None:
    repo = with_last_week(PlacingRepository())
    await run(repo)

    second = await run(repo)

    assert second.placed == 0 and second.message.startswith("Ingen endring.")


# ---- the endpoint -------------------------------------------------------------------------------


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.leader_shifts.log_admin_activity", lambda **kwargs: None
    )
    repo = with_last_week(PlacingRepository())
    client, repo, token = make(monkeypatch, repo=repo)
    return Writer(client, token), repo


def copy(w: Writer, **headers):
    return w.send("POST", f"/weeks/{WEEK}/copy-previous", params={}, **headers)


def test_the_endpoint_copies_and_answers_with_the_board_and_the_skipped(writer) -> None:
    w, repo = writer
    repo.wishes[(1, HALLE)] = "nei"

    response = copy(w)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"].startswith("Kopierte 3 plasser fra uke 44.")
    assert [n["text"] for n in body["warnings"]] == [
        "Halle Hjelper: svarte «kan ikke» for dagskift tirsdag 3.11."
    ]
    assert all(n["level"] == "yellow" for n in body["warnings"])
    assert repo.who(SKIFTLEDER_SLOT) == [SIGRID]


def test_it_needs_the_safety_layer_and_the_current_version(writer) -> None:
    w, repo = writer
    assert copy(w, Origin="https://elsewhere.example").status_code == 403
    assert copy(w, **{"X-CSRF-Token": None}).status_code == 403
    assert copy(w, **{"If-Match": "old"}).status_code == 409
    assert copy(w, **{"If-Match": None}).status_code in (400, 422)
    assert repo.who(SKIFTLEDER_SLOT) == []


def test_a_copy_that_changes_nothing_does_not_mark_the_week_changed(writer) -> None:
    w, repo = writer
    repo.statuses[(7, THIS_WEEK)] = "published"
    first = copy(w)  # fills the week
    assert first.status_code == 200
    repo.changes.clear()

    second = copy(w)

    assert second.status_code == 200 and second.json()["message"].startswith(
        "Ingen endring."
    )
    assert repo.changes == {}


def test_a_failure_halfway_is_rolled_back_not_left_half_done(
    writer, monkeypatch
) -> None:
    w, repo = writer
    rolled_back: list[bool] = []

    async def fake_rollback() -> None:
        rolled_back.append(True)

    calls = {"n": 0}
    real_add = repo.add_assignment

    async def failing_add(slot_id, volunteer_id):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("database went away")
        return await real_add(slot_id, volunteer_id)

    monkeypatch.setattr(
        "app.api.v1.leader_shifts.rollback_request_session", fake_rollback
    )
    monkeypatch.setattr(repo, "add_assignment", failing_add)

    with pytest.raises(RuntimeError):
        copy(w)

    assert rolled_back == [True]
