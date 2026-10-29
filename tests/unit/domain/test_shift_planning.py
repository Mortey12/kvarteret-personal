from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.shifts.models import AssignedPerson, ShiftRecord, SlotRecord
from app.domain.shifts.planning import (
    build_shift_view,
    format_day,
    format_week,
    group_into_days,
    NO_RULES,
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


# Tree: 1 Skjenke -> 7 Stjerne, 8 Grondahls, 9 Halvtimen, 10 Kjokken; 20 is outside Skiftlista.
PARENTS = {1: None, 7: 1, 8: 1, 9: 1, 10: 1, 20: None, 21: 20}
PARTICIPATING = frozenset({1, 7, 8, 9, 10})
NOT_MEMBER = "Kun for medlemmer av gruppa"
TRAINEE = "I opplæring: bruk opplæringsplassen"
APPROVED_SKIFTLEDER = "Kun for godkjente skiftleder"


def _problem(kind, group, members, rows=(), *, open_for_samf=False, switch=False):
    from app.domain.shifts.planning import group_rules, person_eligibility, placement_problem

    person = person_eligibility(members, rows, PARTICIPATING, PARENTS)
    rules = group_rules(PARTICIPATING, PARENTS, {group: switch})
    return placement_problem(kind, group, open_for_samf, person, rules.get(group, NO_RULES))


CASES = [
    # (id, kind, shift group, members, rows, open, switch, expected)
    ("own group skjenker", "skjenker", 7, [7], [("skjenker", "approved", 7)], False, False, None),
    ("sibling bar skjenker", "skjenker", 8, [7], [("skjenker", "approved", 7)], False, False, None),
    ("halvtimen skjenker (20+ is the caller's)", "skjenker", 9, [7], [("skjenker", "approved", 7)], False, False, None),
    ("member without a row", "skjenker", 7, [7], [], False, False, "Kun for godkjente skjenker"),
    ("non-participating group", "skjenker", 21, [7], [("skjenker", "approved", 7)], False, False, NOT_MEMBER),
    ("member of non-participating only", "skjenker", 7, [21], [("skjenker", "approved", 7)], False, False, NOT_MEMBER),
    ("bartender with skjenker row", "bartender", 7, [7], [("skjenker", "approved", 7)], False, False, None),
    ("bartender without row", "bartender", 7, [7], [], False, False, "Kun for godkjente skjenker"),
    ("skiftleder own group", "skiftleder", 7, [7], [("skiftleder", "approved", 7)], False, False, None),
    ("skiftleder other bar, switch off", "skiftleder", 8, [7], [("skiftleder", "approved", 7)], False, False, APPROVED_SKIFTLEDER),
    ("skiftleder other bar, switch on", "skiftleder", 8, [7], [("skiftleder", "approved", 7)], False, True, None),
    ("skiftleder row for shift group, member of sibling only", "skiftleder", 8, [7], [("skiftleder", "approved", 8)], False, False, None),
    ("kokk in the kitchen", "kokk", 10, [10], [("kokk", "approved", 10)], False, False, None),
    ("kokk row in the kitchen held by a bar member", "kokk", 10, [7], [("kokk", "approved", 10)], False, False, None),
    ("kokk row in a bar does not work in the kitchen", "kokk", 10, [7], [("kokk", "approved", 7)], False, True, "Kun for godkjente kokk"),
    ("garderobe in area", "garderobe", 7, [7], [], False, False, None),
    ("garderobe out of area", "garderobe", 21, [7], [], False, False, NOT_MEMBER),
    ("garderobe open for samf", "garderobe", 21, [7], [], True, False, None),
    ("open skjenker for everyone", "skjenker", 7, [], [], True, False, None),
    ("open skjenker for outsider group member", "skjenker", 7, [21], [], True, False, None),
    ("skiftleder never open", "skiftleder", 7, [], [], True, False, NOT_MEMBER),
    ("kokk never open", "kokk", 10, [7], [], True, False, "Kun for godkjente kokk"),
    ("opened skjenker for member without a row", "skjenker", 7, [7], [], True, False, None),
    ("training row for crew place", "skjenker", 7, [7], [("skjenker", "training", 7)], False, False, TRAINEE),
    ("training row for open crew place", "skjenker", 7, [7], [("skjenker", "training", 7)], True, False, TRAINEE),
    ("training row in another bar does not count here", "skjenker", 8, [7], [("skjenker", "training", 7)], False, False, "Kun for godkjente skjenker"),
    ("kokk trainee refused crew place", "kokk", 10, [10], [("kokk", "training", 10)], False, False, TRAINEE),
    ("approved wins over training", "skjenker", 7, [7], [("skjenker", "training", 7), ("skjenker", "approved", 7)], False, False, None),
    ("training row but no longer a member", "skjenker", 7, [], [("skjenker", "training", 7)], False, False, NOT_MEMBER),
    ("former member with rows, no membership", "skjenker", 7, [], [("skjenker", "approved", 7)], False, False, NOT_MEMBER),
    ("registered only in the parent group", "skjenker", 7, [1], [("skjenker", "approved", 7)], False, False, None),
    ("parent only, no row", "skjenker", 7, [1], [], False, False, "Kun for godkjente skjenker"),
    ("opplaering needs a training row", "opplaering", 7, [7], [("skjenker", "training", 7)], False, False, None),
    ("opplaering without a row", "opplaering", 7, [7], [("skjenker", "approved", 7)], False, False, "Kun for de som er i opplæring i gruppen"),
    ("opplaering training row but not a member", "opplaering", 7, [], [("skjenker", "training", 7)], False, False, "Kun for de som er i opplæring i gruppen"),
    ("opplaering never open for samf", "opplaering", 7, [], [], True, False, "Kun for de som er i opplæring i gruppen"),
]


@pytest.mark.parametrize(
    ("kind", "group", "members", "rows", "open_for_samf", "switch", "expected"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_placement_problem_case_table(kind, group, members, rows, open_for_samf, switch, expected) -> None:
    rows = [(q, status, g) for q, status, g in rows]
    assert _problem(kind, group, members, rows, open_for_samf=open_for_samf, switch=switch) == expected


def test_area_is_members_siblings_and_groups_below() -> None:
    from app.domain.shifts.permissions import person_area

    assert person_area([7], PARTICIPATING, PARENTS) == {7, 8, 9, 10}
    assert person_area([1], PARTICIPATING, PARENTS) == {1, 7, 8, 9, 10}
    assert person_area([21], PARTICIPATING, PARENTS) == frozenset()
    assert person_area([], PARTICIPATING, PARENTS) == frozenset()


def test_halvtimen_age_is_checked_by_the_caller_after_the_rule() -> None:
    from app.domain.shifts.planning import age_problem

    assert _problem("skjenker", 9, [7], [("skjenker", "approved", 7)]) is None
    assert age_problem("Halvtimen", date(2007, 1, 1), date(2026, 11, 2)) == "Halvtimen krever 20 år"


def _matrix_people():
    from app.domain.shifts.planning import person_eligibility

    row_sets = [
        [],
        [("skjenker", "approved", 7)],
        [("skjenker", "training", 7)],
        [("skiftleder", "approved", 7)],
        [("kokk", "approved", 10)],
        [("skjenker", "training", 7), ("skjenker", "approved", 7)],
    ]
    for members in ([], [7], [1], [10], [21]):
        for rows in row_sets:
            yield person_eligibility(members, rows, PARTICIPATING, PARENTS)


def test_allowed_tokens_agree_with_placement_problem_across_the_matrix() -> None:
    from app.domain.shifts.planning import SLOT_KINDS, allowed_tokens, group_rules, placement_problem

    groups = sorted(PARTICIPATING) + [21]
    for switch in (False, True):
        rules = group_rules(PARTICIPATING, PARENTS, dict.fromkeys(PARTICIPATING, switch))
        for person in _matrix_people():
            tokens = set(allowed_tokens(person, groups, rules))
            for group in groups:
                for kind in SLOT_KINDS:
                    for open_for_samf in (False, True):
                        ok = placement_problem(kind, group, open_for_samf, person, rules.get(group, NO_RULES)) is None
                        assert (f"{group}:{kind}:{int(open_for_samf)}" in tokens) == ok
