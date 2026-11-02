from __future__ import annotations

from datetime import date

from app.domain.shifts.week_status import (
    can_open_week,
    leader_status,
    mondays_in_month,
    unpublished_week_banner,
)


def test_mondays_in_month() -> None:
    assert mondays_in_month(date(2026, 11, 2)) == [date(2026, 11, d) for d in (2, 9, 16, 23, 30)]
    assert mondays_in_month(date(2026, 2, 16)) == [date(2026, 2, d) for d in (2, 9, 16, 23)]
    assert mondays_in_month(date(2026, 12, 28))[0] == date(2026, 12, 7)


def test_banner_wording_and_urgency() -> None:
    banner = unpublished_week_banner(7, "Stjerne", date(2026, 11, 30), None, date(2026, 11, 20))
    assert banner is not None and not banner.urgent
    assert banner.text == "Uke 49 er ikke publisert. Den blir åpen for alle mandag 30. november."
    urgent = unpublished_week_banner(7, "Stjerne", date(2026, 11, 30), None, date(2026, 11, 27))
    assert urgent is not None and urgent.urgent


def test_banner_names_the_empty_places_and_the_monday() -> None:
    many = unpublished_week_banner(7, "Stjerne", date(2026, 11, 30), None, date(2026, 11, 20), 6)
    assert many is not None and many.empty_places == 6
    assert many.text == "Uke 49 er ikke publisert. 6 plasser er ledige, og de blir åpne for alle mandag 30. november."
    one = unpublished_week_banner(7, "Stjerne", date(2026, 11, 30), None, date(2026, 11, 20), 1)
    assert one is not None and "1 plass er ledig, og de blir åpne for alle mandag" in one.text
    held = unpublished_week_banner(7, "S", date(2026, 11, 30), "open", date(2026, 11, 27), 4)
    assert held is not None and held.text.endswith("4 plasser er ledige.") and "åpne for alle" not in held.text


def test_empty_places_count_open_places_on_open_shifts_only() -> None:
    from datetime import UTC, datetime

    from app.domain.shifts.models import AssignedPerson, ShiftRecord, SlotRecord
    from app.domain.shifts.week_status import empty_places_by_week

    def shift(shift_id, group_id, day, *, closed=False, people=0):
        start = datetime(2026, 11, day, 17, tzinfo=UTC)
        slot = SlotRecord(1, "skjenker", 3, False, 0, [AssignedPerson(i, i, "X", False) for i in range(people)])
        return ShiftRecord(shift_id, group_id, "G", "Stjernesalen", "Skift", start, start, None, closed, False, [slot])

    found = empty_places_by_week(
        [shift(1, 7, 30, people=1), shift(2, 7, 30, closed=True), shift(3, 8, 30, people=3), shift(4, 7, 3)]
    )
    assert found == {(7, date(2026, 11, 30)): 2, (8, date(2026, 11, 30)): 0, (7, date(2026, 11, 2)): 3}


def test_no_banner_when_published_too_far_ahead_or_current() -> None:
    week = date(2026, 11, 30)
    assert unpublished_week_banner(7, "S", week, "published", date(2026, 11, 27)) is None
    assert unpublished_week_banner(7, "S", week, None, date(2026, 11, 10)) is None
    assert unpublished_week_banner(7, "S", date(2026, 11, 23), None, date(2026, 11, 25)) is None


def test_explicit_unpublished_row_gets_a_different_text() -> None:
    banner = unpublished_week_banner(7, "S", date(2026, 11, 30), "open", date(2026, 11, 27))
    assert banner is not None and "åpen for ønsker" in banner.text


def test_legacy_allocating_row_reads_as_open_for_leaders() -> None:
    banner = unpublished_week_banner(7, "S", date(2026, 11, 30), "allocating", date(2026, 11, 27))
    assert banner is not None and "åpen for ønsker" in banner.text and "fordeles" not in banner.text
    assert leader_status("allocating") == "open"
    assert leader_status("published") == "published"


def test_only_later_weeks_can_be_opened() -> None:
    today = date(2026, 11, 4)  # Wednesday of the week of 2 Nov
    assert not can_open_week(date(2026, 10, 26), today)
    assert not can_open_week(date(2026, 11, 2), today)
    assert can_open_week(date(2026, 11, 9), today)
