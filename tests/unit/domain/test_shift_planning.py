from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.shifts.models import AssignedPerson, ShiftRecord, SlotRecord
from app.domain.shifts.planning import (
    build_shift_view,
    format_day,
    format_week,
    group_into_days,
    monday_of,
    open_places,
    training_places_used,
)


def _person(volunteer_id: int) -> AssignedPerson:
    return AssignedPerson(
        assignment_id=volunteer_id, volunteer_id=volunteer_id, name=f"P{volunteer_id}", swap_requested=False
    )


def _shift(
    *,
    crew: int = 1,
    crew_people: int = 0,
    trainees: int = 0,
    replaces: bool = True,
    venue: str = "Stjernesalen",
    starts_at: datetime | None = None,
    closed: bool = False,
) -> ShiftRecord:
    start = starts_at or datetime(2026, 11, 2, 10, 30, tzinfo=UTC)
    return ShiftRecord(
        shift_id=1,
        group_id=1,
        group_name="Stjerne",
        venue=venue,
        title="Dagskift",
        starts_at=start,
        ends_at=start.replace(hour=16),
        note=None,
        is_closed=closed,
        training_replaces_slot=replaces,
        slots=[
            SlotRecord(1, "skiftleder", 1, False, 0, [_person(1)]),
            SlotRecord(2, "skjenker", crew, True, 1, [_person(10 + i) for i in range(crew_people)]),
            SlotRecord(3, "opplaering", 1, False, 2, [_person(20 + i) for i in range(trainees)]),
        ],
    )


def test_trainee_takes_a_regular_place() -> None:
    shift = _shift(crew=1, trainees=1)
    crew, training = shift.slots[1], shift.slots[2]
    assert open_places(shift, crew) == 0
    assert training_places_used(shift, crew) == 1
    assert open_places(shift, training) == 0


def test_full_crew_leaves_no_room_for_a_trainee() -> None:
    shift = _shift(crew=1, crew_people=1)
    assert open_places(shift, shift.slots[2]) == 0
    view = build_shift_view(shift)
    assert view.slots[2].blocked_by_training is True


def test_trainee_on_top_when_shift_says_so() -> None:
    shift = _shift(crew=1, crew_people=1, replaces=False)
    assert open_places(shift, shift.slots[2]) == 1
    assert training_places_used(shift, shift.slots[1]) == 0


def test_quiz_shift_with_trainee_keeps_one_crew_place_open() -> None:
    shift = _shift(crew=2, trainees=1)
    assert open_places(shift, shift.slots[1]) == 1
    view = build_shift_view(shift)
    assert view.slots[1].filled_text == "1/2 (1 opplæring)"
    assert view.open_places == 1


def test_closed_shift_has_no_open_places() -> None:
    view = build_shift_view(_shift(closed=True))
    assert view.open_places == 0
    assert all(slot.open_places == 0 for slot in view.slots)


def test_days_are_grouped_by_oslo_date_and_venue_order() -> None:
    late = _shift(venue="Grøndahls", starts_at=datetime(2026, 11, 2, 18, 30, tzinfo=UTC))
    early = _shift(venue="Stjernesalen", starts_at=datetime(2026, 11, 2, 10, 30, tzinfo=UTC))
    days = group_into_days([late, early])
    assert len(days) == 1
    assert days[0].label == "Mandag 2. november"
    assert [venue.label for venue in days[0].venues] == ["Stjernesalen og kjøkken", "Grøndahls"]


def test_week_helpers() -> None:
    assert monday_of(date(2026, 11, 5)) == date(2026, 11, 2)
    assert format_week(date(2026, 11, 2)) == "Uke 45 · 2.–8. november"
    assert format_week(date(2026, 10, 26)) == "Uke 44 · 26. oktober–1. november"
    assert format_day(date(2026, 10, 27)) == "Tirsdag 27. oktober"


def test_only_skiftledere_take_skiftleder_places() -> None:
    from app.domain.shifts.planning import placement_problem

    assert placement_problem("skiftleder", 1, False, {1: {"Skiftleder"}}) is None
    assert placement_problem("skiftleder", 1, False, {1: {"Skjenker"}}) == "Kun for skiftledere i gruppen"
    assert placement_problem("skiftleder", 1, False, {2: {"Skiftleder"}}) is not None


def test_trainees_use_the_training_place() -> None:
    from app.domain.shifts.planning import placement_problem

    assert placement_problem("opplaering", 1, False, {1: {"Opplæring"}}) is None
    assert placement_problem("skjenker", 1, True, {1: {"Opplæring"}}) == "I opplæring: bruk opplæringsplassen"


def test_crew_places_are_for_skjenke_unless_open_for_samf() -> None:
    from app.domain.shifts.planning import placement_problem

    assert placement_problem("skjenker", 1, False, {2: {"Skjenker"}}) is None
    assert placement_problem("skjenker", 1, False, {}) == "Kun for Skjenke"
    assert placement_problem("skjenker", 1, True, {}) is None
    assert placement_problem("kokk", 1, True, {1: {"Skjenker"}}) == "Kun for kokker"


def test_allowed_tokens_match_the_rules() -> None:
    from app.domain.shifts.planning import allowed_tokens

    tokens = allowed_tokens({1: {"Skiftleder"}}, [1, 2])
    assert "1:skiftleder:0" in tokens
    assert "2:skiftleder:0" not in tokens
    assert "2:skjenker:0" in tokens
    assert "1:kokk:1" not in tokens
