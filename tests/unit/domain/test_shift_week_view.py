from __future__ import annotations

import json
from datetime import UTC, date, datetime

from app.domain.shifts.models import AssignedPerson, ShiftRecord, SlotRecord
from app.domain.shifts.planning import group_into_days
from app.domain.shifts.week_view import (
    MemberInput,
    WeekInput,
    build_week_view,
    month_weeks,
    status_text,
    week_label,
)

MONDAY = date(2026, 11, 2)
BAR = 7


def _shift(
    shift_id: int,
    day: int,
    start_hour: int,
    end_hour: int,
    title: str,
    people: list[tuple[int, str]] | None = None,
    *,
    sl: tuple[int, str] | None = None,
    crew_count: int = 2,
    closed: bool = False,
) -> ShiftRecord:
    # November 2026 is CET (UTC+1): 10:00 UTC is 11:00 in Oslo.
    start = datetime(2026, 11, day, start_hour - 1, 0, tzinfo=UTC)
    end = datetime(2026, 11, day, end_hour - 1, 0, tzinfo=UTC)
    sl_people = [AssignedPerson(shift_id * 100, sl[0], sl[1], False)] if sl else []
    crew = [AssignedPerson(shift_id * 100 + 1 + i, vid, name, False) for i, (vid, name) in enumerate(people or [])]
    return ShiftRecord(
        shift_id=shift_id,
        group_id=BAR,
        group_name="Stjerne",
        venue="Stjernesalen",
        title=title,
        starts_at=start,
        ends_at=end,
        note=None,
        is_closed=closed,
        training_replaces_slot=False,
        slots=[
            # The crew slot has the lower sort order on purpose: the grid still puts the leader first.
            SlotRecord(shift_id * 10 + 1, "skjenker", crew_count, False, 0, crew),
            SlotRecord(shift_id * 10, "skiftleder", 1, False, 1, sl_people),
        ],
    )


def _members() -> list[MemberInput]:
    return [
        MemberInput(1, "Ada Alfa", ("7:skiftleder:0", "7:skjenker:0")),
        MemberInput(2, "Bo Beta", ("7:skjenker:0",)),
        MemberInput(3, "Cy Gamma", ("7:skjenker:0",)),
        MemberInput(4, "Di Delta", ("7:skjenker:0",)),
    ]


def _week(records: list[ShiftRecord], **overrides) -> WeekInput:
    base = dict(
        bar_id=BAR,
        bar_name="Stjerne",
        week_start=MONDAY,
        days=group_into_days(records),
        members=_members(),
        answers={},
        shifts_per_day={},
        month_counts={},
        began=frozenset(),
        status="open",
        strip_statuses={},
        approved_skiftleder_token="7:skiftleder:0",
    )
    base.update(overrides)
    return WeekInput(**base)


def _two_a_day() -> list[ShiftRecord]:
    return [
        _shift(1, 2, 11, 17, "Dagskift", [(2, "Bo Beta")], sl=(1, "Ada Alfa")),
        _shift(2, 2, 17, 22, "Kveldsskift"),
        _shift(3, 3, 11, 17, "Dagskift"),
        _shift(4, 3, 17, 22, "Kveldsskift"),
        _shift(5, 7, 14, 20, "Dagskift"),  # Saturday: only one shift that day
    ]


def test_week_label_and_month_border() -> None:
    assert week_label(date(2026, 11, 2)) == "Uke 45 (2.–8. nov)"
    assert week_label(date(2026, 11, 30)) == "Uke 49 (30. nov–6. des)"


def test_rows_are_positions_in_the_day_with_grey_days() -> None:
    view = build_week_view(_week(_two_a_day()))

    assert [row.position for row in view.rows] == [1, 2]
    assert [(row.title, row.time_label) for row in view.rows] == [
        ("Dagskift", "11:00–17:00"),
        ("Kveldsskift", "17:00–22:00"),
    ]
    first, second = view.rows
    # Monday, Tuesday and Saturday have a 1st shift; Wednesday to Friday and Sunday have none.
    assert [cell is not None for cell in first.cells] == [True, True, False, False, False, True, False]
    assert [cell is not None for cell in second.cells] == [True, True, False, False, False, False, False]
    assert [head.name for head in view.heads][0] == "Mandag"
    assert view.heads[0].date_label == "2. nov"


def test_row_label_is_the_most_common_title_and_time_of_the_position() -> None:
    records = [
        _shift(1, 2, 11, 17, "Dagskift"),
        _shift(2, 3, 11, 17, "Dagskift"),
        _shift(3, 4, 12, 18, "Quizskift"),
    ]
    view = build_week_view(_week(records))

    assert (view.rows[0].title, view.rows[0].time_label) == ("Dagskift", "11:00–17:00")


def test_row_label_tie_goes_to_the_earliest_day() -> None:
    records = [_shift(1, 3, 12, 18, "Sen"), _shift(2, 2, 11, 17, "Tidlig")]
    view = build_week_view(_week(records))

    assert view.rows[0].title == "Tidlig"


def test_shifts_in_two_rooms_are_ordered_by_start_time() -> None:
    late = _shift(1, 2, 19, 23, "Kveld")
    late.venue = "Grøndahls"
    early = _shift(2, 2, 11, 17, "Dag")
    view = build_week_view(_week([late, early]))

    assert [row.title for row in view.rows] == ["Dag", "Kveld"]


def test_cell_lists_skiftleder_first_then_crew_then_open_places() -> None:
    view = build_week_view(_week(_two_a_day()))
    cell = view.rows[0].cells[0]

    assert [(place.kind, place.name or "Ledig") for place in cell.places] == [
        ("skiftleder", "Ada Alfa"),
        ("skjenker", "Bo Beta"),
        ("skjenker", "Ledig"),
    ]
    assert cell.places[0].token == "7:skiftleder:0"
    assert cell.open_places == 1


def test_answer_words_and_counts_are_among_the_bars_members_only() -> None:
    answers = {(1, 1): "vil", (1, 2): "kan", (1, 3): "nei", (1, 4): "vil", (1, 99): "vil"}
    view = build_week_view(_week(_two_a_day(), answers=answers))
    cell = view.rows[0].cells[0]

    assert (cell.vil, cell.kan) == (2, 1)  # person 99 is not a member of the bar
    words = {place.name: place.answer_word for place in cell.places if place.name}
    assert words == {"Ada Alfa": "vil", "Bo Beta": "kan"}
    assert view.rows[1].cells[0].places[0].is_open


def test_unanswered_member_reads_ikke_svart_and_outsider_reads_nothing() -> None:
    records = [_shift(1, 2, 11, 17, "Dagskift", [(2, "Bo Beta"), (50, "Utenfor Lista")])]
    view = build_week_view(_week(records))
    words = {place.name: place.answer_word for place in view.rows[0].cells[0].places if place.name}

    assert words == {"Bo Beta": "ikke svart", "Utenfor Lista": ""}


def test_problems_red_for_kan_ikke_and_same_day_yellow_for_second_shift_in_week() -> None:
    records = [
        _shift(1, 2, 11, 17, "Dagskift", [(2, "Bo Beta"), (3, "Cy Gamma")]),
        _shift(2, 2, 17, 22, "Kveldsskift", [(3, "Cy Gamma")]),
        _shift(3, 4, 11, 17, "Dagskift", [(2, "Bo Beta")]),
    ]
    per_day = {(2, date(2026, 11, 2)): 1, (2, date(2026, 11, 4)): 1, (3, date(2026, 11, 2)): 2}
    view = build_week_view(_week(records, answers={(1, 2): "nei"}, shifts_per_day=per_day))

    monday_day = {place.name: place for place in view.rows[0].cells[0].places if place.name}
    bo = monday_day["Bo Beta"]
    assert [(p.text, p.level) for p in bo.problems] == [
        ("svarte kan ikke", "red"),
        ("2 skift denne uka", "yellow"),
    ]
    assert bo.level == "red"
    cy = monday_day["Cy Gamma"]
    assert [(p.text, p.level) for p in cy.problems] == [
        ("to skift samme dag", "red"),
        ("2 skift denne uka", "yellow"),
    ]
    # Cy's shift count comes from the all-groups map, not only this bar: 2 on Monday.
    # Bo on Wednesday has only the yellow week hint.
    wednesday = next(place for place in view.rows[0].cells[2].places if place.name == "Bo Beta")
    assert wednesday.level == "yellow"
    # The red count only includes places with a red problem; yellow-only places add to problem_count alone.
    assert 0 < view.problem_red_count < view.problem_count


def test_board_warning_about_extra_shifts_is_not_repeated_but_other_warnings_show() -> None:
    record = _shift(1, 2, 11, 17, "Dagskift", [(2, "Bo Beta")])
    record.slots[0].people[0].warnings = ["har allerede 1 skift denne uka", "fødselsdato mangler, sjekk alderen"]
    view = build_week_view(_week([record]))
    problems = [p.text for p in view.rows[0].cells[0].places[1].problems]

    assert problems == ["fødselsdato mangler, sjekk alderen"]


def test_kan_ikke_i_dag_and_swap_request_are_marked() -> None:
    record = _shift(1, 2, 11, 17, "Dagskift", [(2, "Bo Beta"), (3, "Cy Gamma")])
    record.slots[0].people[0].cannot_attend = True
    record.slots[0].people[1].swap_requested = True
    places = {p.name: p for p in build_week_view(_week([record])).rows[0].cells[0].places if p.name}

    assert places["Bo Beta"].level == "red" and places["Cy Gamma"].level == "yellow"


def test_chips_count_problems_open_places_and_people_without_shift() -> None:
    records = _two_a_day()
    per_day = {(2, date(2026, 11, 2)): 1}
    view = build_week_view(
        _week(records, shifts_per_day=per_day, answers={(1, 2): "nei"}, began=frozenset({2, 3}))
    )

    assert view.problem_count == 1
    assert view.problem_red_count == 1  # Bo answered kan ikke
    assert view.open_places == 1 + 4 * 3  # shift 1 has 1 open; shifts 2 to 5 have 3 each
    assert view.without_shift == ["Cy Gamma"]  # began, no shift; Bo has one


def test_closed_shift_has_no_open_places_in_the_chip() -> None:
    view = build_week_view(_week([_shift(1, 2, 11, 17, "Dagskift", closed=True)]))

    assert view.open_places == 0 and view.rows[0].cells[0].is_closed


def test_people_panel_rows_and_began_counts() -> None:
    view = build_week_view(
        _week(
            _two_a_day(),
            shifts_per_day={(2, date(2026, 11, 2)): 1, (1, date(2026, 11, 2)): 1, (1, date(2026, 11, 3)): 1},
            month_counts={1: 5, 2: 3},
            began=frozenset({1, 3}),
        )
    )

    assert (view.began_count, view.member_count) == (2, 4)
    assert view.missing_names == ["Bo Beta", "Di Delta"]
    by_name = {person.name: person for person in view.people}
    ada = by_name["Ada Alfa"]
    assert (ada.is_sl, ada.week_count, ada.month_count, ada.status_word) == (True, 2, 5, "har begynt")
    assert not by_name["Bo Beta"].is_sl and by_name["Bo Beta"].status_word == "ikke svart"
    # Fewest this week first, then fewest this month.
    assert [person.name for person in view.people] == ["Cy Gamma", "Di Delta", "Bo Beta", "Ada Alfa"]


def test_week_status_badge_text() -> None:
    assert status_text("published", True) == "Publisert"
    assert status_text("open", True) == "Utkast: ikke synlig i appen"
    assert status_text("open", False) == "Åpen for ønsker"
    published = build_week_view(_week(_two_a_day(), status="published"))
    assert published.status_text == "Publisert"
    assert build_week_view(_week(_two_a_day())).status_text == "Utkast: ikke synlig i appen"


def test_strip_lists_the_months_weeks_with_their_status() -> None:
    assert month_weeks(date(2026, 11, 1)) == [
        date(2026, 11, 2),
        date(2026, 11, 9),
        date(2026, 11, 16),
        date(2026, 11, 23),
    ]
    # 30 Nov to 6 Dec has its Thursday in December, so it counts for December.
    assert month_weeks(date(2026, 12, 1))[0] == date(2026, 11, 30)
    statuses = {date(2026, 11, 2): "published", date(2026, 11, 9): "open"}
    strip = build_week_view(_week(_two_a_day(), strip_statuses=statuses)).strip

    assert [(item.number, item.status_label, item.is_shown) for item in strip] == [
        (45, "Publisert", True),
        (46, "Åpen for ønsker", False),
        (47, "Åpen for ønsker", False),
        (48, "Åpen for ønsker", False),
    ]


def test_json_block_has_answers_people_and_places_and_is_serialisable() -> None:
    view = build_week_view(
        _week(
            _two_a_day(),
            answers={(1, 2): "kan", (1, 3): "nei"},
            shifts_per_day={(2, date(2026, 11, 2)): 1, (2, date(2026, 11, 3)): 1},
            month_counts={2: 4},
            began=frozenset({2}),
        )
    )
    data = json.loads(json.dumps(view.data))

    assert data["bar_id"] == BAR and data["week_start"] == "2026-11-02"
    shift_one = next(item for item in data["shifts"] if item["shift_id"] == 1)
    assert shift_one["answers"] == {"2": "kan", "3": "nei"}
    assert shift_one["position"] == 1 and shift_one["day"] == "2026-11-02"
    assert shift_one["places"][0] == {
        "slot_id": 10,
        "kind": "skiftleder",
        "token": "7:skiftleder:0",
        "assignment_id": 100,
        "volunteer_id": 1,
    }
    bo = next(person for person in data["people"] if person["volunteer_id"] == 2)
    assert bo == {
        "volunteer_id": 2,
        "name": "Bo Beta",
        "sl": False,
        "week_shifts": 2,
        "month_shifts": 4,
        "began": True,
        "busy_days": ["2026-11-02", "2026-11-03"],
        "allowed": ["7:skjenker:0"],
    }
    assert next(p for p in data["people"] if p["volunteer_id"] == 1)["sl"] is True


def test_empty_week_has_no_rows() -> None:
    view = build_week_view(_week([]))

    assert view.rows == [] and view.problem_count == 0 and view.open_places == 0
    assert view.data["shifts"] == []


def test_builder_is_deterministic() -> None:
    first = build_week_view(_week(_two_a_day(), answers={(1, 2): "vil"}))
    second = build_week_view(_week(_two_a_day(), answers={(1, 2): "vil"}))

    assert first == second
