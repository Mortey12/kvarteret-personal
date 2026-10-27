from __future__ import annotations

from datetime import date

from app.domain.shifts.week_status import mondays_in_month, unpublished_week_banner


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


def test_no_banner_when_published_too_far_ahead_or_current() -> None:
    week = date(2026, 11, 30)
    assert unpublished_week_banner(7, "S", week, "published", date(2026, 11, 27)) is None
    assert unpublished_week_banner(7, "S", week, None, date(2026, 11, 10)) is None
    assert unpublished_week_banner(7, "S", date(2026, 11, 23), None, date(2026, 11, 25)) is None


def test_explicit_unpublished_row_gets_a_different_text() -> None:
    banner = unpublished_week_banner(7, "S", date(2026, 11, 30), "allocating", date(2026, 11, 27))
    assert banner is not None and "fordeles nå" in banner.text
