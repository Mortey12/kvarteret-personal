from __future__ import annotations

from app.domain.shifts.leader_board import to_board
from tests.support.leader_week import leader_week_view


def _board():
    return to_board(leader_week_view())


def test_header_rows_and_status() -> None:
    board = _board()

    assert (board.bar.id, board.bar.name) == (7, "Stjerne")
    assert (board.week_start, board.week_number, board.status) == ("2026-11-02", 45, "open")
    assert [(row.position, row.label, row.time_label) for row in board.rows] == [(1, "Dagskift", "11:00–17:00")]
    assert board.deadline is None and board.last_changed is None


def test_a_week_status_other_than_open_or_published_counts_as_open() -> None:
    strip = {item.week_start: item.status for item in _board().strip}

    assert strip["2026-11-09"] == "published"
    assert strip["2026-11-16"] == "open"


def test_places_share_a_slot_id_but_get_their_own_seat() -> None:
    monday = _board().shifts[0]
    crew = [place for place in monday.places if place.kind == "skjenker"]

    assert [place.slot_id for place in crew] == [11, 11, 11]
    assert [place.seat for place in crew] == [0, 1, 2]
    assert [place.assignment is not None for place in crew] == [True, False, False]


def test_a_placed_person_carries_the_servers_problems_and_flags() -> None:
    monday = _board().shifts[0]
    ada = next(place for place in monday.places if place.kind == "skiftleder").assignment
    bo = next(place for place in monday.places if place.kind == "skjenker").assignment

    assert ada is not None and bo is not None
    assert (ada.assignment_id, ada.volunteer_id, ada.cannot_attend, ada.swap_requested) == (101, 1, False, False)
    assert [(note.text, note.level) for note in ada.problems] == [("svarte kan ikke", "red")]
    assert bo.swap_requested is True
    assert any(note.text == "ønsker bytte" for note in bo.problems)


def test_kan_ikke_i_dag_is_kept_on_the_place() -> None:
    tuesday = _board().shifts[1]
    cy = next(place for place in tuesday.places if place.assignment).assignment

    assert cy is not None
    assert (cy.volunteer_id, cy.cannot_attend) == (3, True)
    assert any(note.text == "kan ikke i dag" and note.level == "red" for note in cy.problems)


def test_answers_are_text_keyed_and_only_for_answered_people() -> None:
    board = _board()

    assert board.shifts[0].answers == {"1": "nei", "2": "vil"}
    assert board.shifts[1].answers == {}


def test_people_and_their_counts() -> None:
    people = {person.volunteer_id: person for person in _board().people}

    assert people[1].is_skiftleder and not people[2].is_skiftleder
    assert (people[1].week_shifts, people[1].month_shifts) == (1, 3)
    assert people[1].answer_status == "paabegynt" and people[4].answer_status == "ikke_svart"
    assert people[1].busy_days == ["2026-11-02"]
    assert people[4].busy_days == []


def test_fit_is_passed_through_from_the_servers_place_state() -> None:
    fit = _board().fit["4"]

    assert fit["21"].model_dump() == {"ok": False, "reason": "Kun for godkjente skiftledere"}
    assert fit["22"].model_dump() == {
        "ok": True,
        "warnings": [{"text": "skift nummer 2 denne uka", "level": "yellow"}],
        "needs_confirmation": False,
    }


def test_a_place_the_person_lacks_the_qualification_for_needs_confirmation() -> None:
    fit = _board().fit["4"]["23"]

    assert fit.model_dump() == {
        "ok": True,
        "warnings": [{"text": "Kun for godkjente skiftledere", "level": "red"}],
        "needs_confirmation": True,
    }
    assert _board().fit["4"]["22"].needs_confirmation is False


def test_shift_times_are_real_timestamps_in_oslo_time() -> None:
    from datetime import UTC, datetime

    times = {1: (datetime(2026, 11, 2, 10, 0, tzinfo=UTC), datetime(2026, 11, 2, 16, 0, tzinfo=UTC))}
    first, second = to_board(leader_week_view(), times=times).shifts

    assert first.starts_at == "2026-11-02T11:00:00+01:00"
    assert first.ends_at == "2026-11-02T17:00:00+01:00"
    assert first.duration_minutes == 360
    # A shift whose times are unknown says so instead of guessing.
    assert (second.starts_at, second.ends_at, second.duration_minutes) == (None, None, None)


def test_a_shift_past_midnight_and_a_clock_change_get_the_right_length() -> None:
    from datetime import UTC, datetime

    # Saturday 24 October 2026 22:00 to 02:00 Sunday 25 October: the clocks go back at 03:00,
    # so this is still 4 hours. The night of 24 to 25 October has no extra hour before 03:00.
    pub = (datetime(2026, 10, 24, 20, 0, tzinfo=UTC), datetime(2026, 10, 25, 0, 0, tzinfo=UTC))
    # Starting 01:00 Oslo time and ending 04:00 the same night is 4 hours of real time.
    night = (datetime(2026, 10, 24, 23, 0, tzinfo=UTC), datetime(2026, 10, 25, 3, 0, tzinfo=UTC))
    first, _ = to_board(leader_week_view(), times={1: pub}).shifts
    other, _ = to_board(leader_week_view(), times={1: night}).shifts

    assert first.duration_minutes == 240 and first.ends_at.endswith("+02:00")
    assert other.duration_minutes == 240 and other.starts_at.endswith("+02:00") and other.ends_at.endswith("+01:00")


def test_week_minutes_come_from_the_loader() -> None:
    people = {person.volunteer_id: person for person in to_board(leader_week_view(), week_minutes={1: 600}).people}

    assert people[1].week_minutes == 600 and people[4].week_minutes == 0


def test_a_seat_of_someone_who_cannot_come_is_drawn_once() -> None:
    tuesday = _board().shifts[1]
    crew = [place for place in tuesday.places if place.kind == "skjenker"]

    # Room for three: Cy (kan ikke i dag) plus two open seats, not three more.
    assert len(crew) == 3
    assert [place.seat for place in crew] == [0, 1, 2]
    assert [place.assignment is not None for place in crew] == [True, False, False]
    assert crew[0].assignment is not None and crew[0].assignment.cannot_attend


def test_needed_places_are_the_ones_that_must_be_filled() -> None:
    board = _board()
    monday, tuesday = board.shifts

    # Monday: skjenker has Bo and two open seats; skiftleder is filled; the Opplæring place is
    # empty but never needed.
    assert [(p.kind, p.needed) for p in monday.places if p.assignment is None] == [
        ("skjenker", True),
        ("skjenker", True),
        ("opplaering", False),
    ]
    # Tuesday: Cy's seat (kan ikke i dag) and the open seats and the empty skiftleder place.
    assert sum(place.needed for place in tuesday.places) == 4
    assert all(not place.needed for place in monday.places if place.assignment and not place.assignment.cannot_attend)
    assert sum(place.needed for shift in board.shifts for place in shift.places) == 2 + 4


def test_a_closed_shift_needs_nobody() -> None:
    view = leader_week_view()
    for row in view.rows:
        for cell in row.cells:
            if cell:
                cell.is_closed = True

    assert not any(place.needed for shift in to_board(view).shifts for place in shift.places)


def test_version_is_stable_and_changes_with_the_content() -> None:
    first, again = _board(), _board()
    assert first.version == again.version and len(first.version) == 16

    changed = leader_week_view()
    changed.data["place_state"]["4"]["22"] = {"ok": 0, "why": "stengt"}
    assert to_board(changed).version != first.version
