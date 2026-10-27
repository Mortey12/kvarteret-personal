"""Allocation status of a week as leaders see and change it (pure rules)."""

from __future__ import annotations

from datetime import date, timedelta

from app.domain.shifts.app_rules import (
    PERIOD_STATUSES,
    STATUS_PUBLISHED,
    effective_week_status,
)
from app.domain.shifts.models import WeekBanner
from app.domain.shifts.planning import format_day, monday_of

SETTABLE_STATUSES = frozenset(PERIOD_STATUSES)
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


def is_past_week(week: date, today: date) -> bool:
    return week < monday_of(today)


def unpublished_week_banner(
    group_id: int, group_name: str, week: date, stored: str | None, today: date
) -> WeekBanner | None:
    """A reminder when a coming week, at most 10 days ahead, is not published."""
    days_ahead = (week - today).days
    if week <= monday_of(today) or days_ahead > BANNER_DAYS:
        return None
    status = effective_week_status(stored, week, today)
    if status == STATUS_PUBLISHED:
        return None
    number = week.isocalendar().week
    if stored is None:
        text = f"Uke {number} er ikke publisert. Den blir åpen for alle {format_day(week).lower()}."
    else:
        text = (
            f"Uke {number} er ikke publisert ({PERIOD_STATUSES[status][0].lower()}). "
            f"Skiftene vises ikke i appen før uka er publisert."
        )
    return WeekBanner(
        group_id=group_id,
        group_name=group_name,
        week_start=week,
        text=text,
        urgent=days_ahead <= URGENT_DAYS,
    )
