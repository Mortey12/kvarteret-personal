"""Allocation status of a week as leaders see and change it (pure rules)."""

from __future__ import annotations

from datetime import date, timedelta

from app.domain.shifts.app_rules import (
    PERIOD_STATUSES,
    STATUS_OPEN,
    STATUS_PUBLISHED,
    effective_week_status,
)
from app.domain.shifts.models import ShiftRecord, WeekBanner
from app.domain.shifts.planning import OSLO, format_day, monday_of, open_places

SETTABLE_STATUSES = frozenset({STATUS_OPEN, STATUS_PUBLISHED})
BANNER_DAYS = 10
URGENT_DAYS = 3


def mondays_in_month(day: date) -> list[date]:
    """Every Monday in the calendar month of ``day``."""
    first = day.replace(day=1)
    monday = first + timedelta(days=(7 - first.weekday()) % 7)
    result: list[date] = []
    while monday.month == day.month:
        result.append(monday)
        monday += timedelta(days=7)
    return result


def leader_status(status: str) -> str:
    """Leaders only know open and published; an old "allocating" row counts as open."""
    return status if status in SETTABLE_STATUSES else STATUS_OPEN


def can_open_week(week: date, today: date) -> bool:
    """Only a week after the current one can be opened for wishes."""
    return week > monday_of(today)


def is_past_week(week: date, today: date) -> bool:
    return week < monday_of(today)


def empty_places_by_week(records: list[ShiftRecord]) -> dict[tuple[int, date], int]:
    """(group id, Monday) -> places still empty on open shifts. Every week with a shift gets a key,
    also when it is full. Closed shifts cannot be claimed, so their places do not count."""
    found: dict[tuple[int, date], int] = {}
    for record in records:
        key = (record.group_id, monday_of(record.starts_at.astimezone(OSLO).date()))
        empty = 0 if record.is_closed else sum(open_places(record, slot) for slot in record.slots)
        found[key] = found.get(key, 0) + empty
    return found


def _places_text(empty: int) -> str:
    return "1 plass er ledig" if empty == 1 else f"{empty} plasser er ledige"


def unpublished_week_banner(
    group_id: int,
    group_name: str,
    week: date,
    stored: str | None,
    today: date,
    empty_places: int = 0,
) -> WeekBanner | None:
    """A big reminder when a coming week, at most 10 days ahead, is not published.

    It names the consequence: without a row the week publishes itself on its Monday and the
    empty places become open for everyone; with an explicit row it stays hidden in the app.
    """
    days_ahead = (week - today).days
    if week <= monday_of(today) or days_ahead > BANNER_DAYS:
        return None
    status = leader_status(effective_week_status(stored, week, today))
    if status == STATUS_PUBLISHED:
        return None
    number = week.isocalendar().week
    monday = format_day(week).lower()
    if stored is None:
        if empty_places:
            text = (
                f"Uke {number} er ikke publisert. {_places_text(empty_places)}, "
                f"og de blir åpne for alle {monday}."
            )
        else:
            text = f"Uke {number} er ikke publisert. Den blir åpen for alle {monday}."
    else:
        text = (
            f"Uke {number} er ikke publisert ({PERIOD_STATUSES[status][0].lower()}). "
            f"Skiftene vises ikke i appen før uka er publisert."
        )
        if empty_places:
            text += f" {_places_text(empty_places)}."
    return WeekBanner(
        group_id=group_id,
        group_name=group_name,
        week_start=week,
        text=text,
        urgent=days_ahead <= URGENT_DAYS,
        empty_places=empty_places,
    )
