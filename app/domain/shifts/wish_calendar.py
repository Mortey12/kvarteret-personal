"""Pure rules for the wish calendar: months, refusals and the month view.

No I/O. The service (``app_wishes.py``) loads shifts, answers and week
statuses and passes them in.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.domain.shifts.app_models import (
    ShiftWishCalendarResponse,
    WishCalendarGroup,
    WishCalendarMine,
    WishCalendarShift,
    WishMonthOption,
)
from app.domain.shifts.app_rules import (
    WISHES_ACCEPTED,
    add_months,
    effective_week_status,
    oslo_midnight,
    week_start,
    wish_window_end,
)
from app.domain.shifts.models import AnsweredView, ShiftRecord
from app.domain.shifts.planning import MONTHS_NO, OSLO, format_time_range

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

NOT_FOUND_TEXT = "Finner ikke skiftet."
STARTED_TEXT = "Skiftet har allerede vært."
PUBLISHED_TEXT = "Planen for denne uka er publisert. Be om bytte i stedet."


def parse_month(text: str) -> date | None:
    """``"2026-12"`` to the first day of that month; None when it is not a month."""
    found = _MONTH.match(text)
    return date(int(found.group(1)), int(found.group(2)), 1) if found else None


def month_key(first: date) -> str:
    return f"{first.year:04d}-{first.month:02d}"


def month_label(first: date) -> str:
    return f"{MONTHS_NO[first.month - 1].capitalize()} {first.year}"


def month_of(moment: datetime) -> date:
    """The first day of the Oslo month a moment falls in."""
    local = moment.astimezone(OSLO)
    return date(local.year, local.month, 1)


def month_options(today: date) -> list[date]:
    """The months a person may switch to: this month through the month of the wish window end."""
    last = wish_window_end(today)
    current = date(today.year, today.month, 1)
    months = [current]
    while months[-1] < date(last.year, last.month, 1):
        months.append(add_months(months[-1], 1))
    return months


def month_bounds(first: date) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of an Oslo month as aware datetimes."""
    return oslo_midnight(first), oslo_midnight(add_months(first, 1))


def refusal_reason(*, visible: bool, started: bool, status: str) -> str | None:
    """Why one wish cannot be set, with the same rules and texts as the single call."""
    if not visible:
        return NOT_FOUND_TEXT
    if started:
        return STARTED_TEXT
    if status not in WISHES_ACCEPTED:
        return PUBLISHED_TEXT
    return None


def refusal_text(reasons: list[str], total: int) -> str:
    """The first reason, and how many of the shifts were refused."""
    return f"{reasons[0]} ({len(reasons)} av {total} skift kunne ikke endres. Ingenting er lagret.)"


def week_month(week: date) -> date:
    """The month a week counts for: the month of its Thursday (ISO), so 30 Nov to 6 Dec is December."""
    thursday = week + timedelta(days=3)
    return date(thursday.year, thursday.month, 1)


def answered_summary(
    *,
    group_id: int,
    group_name: str,
    month: date,
    members: dict[int, str],
    answered: set[int],
) -> AnsweredView:
    """``members`` (volunteer id -> name) are the people who may answer for the bar; the rest is counted."""
    missing = sorted((name for vid, name in members.items() if vid not in answered), key=str.casefold)
    return AnsweredView(
        group_id=group_id,
        group_name=group_name,
        month_label=month_label(month),
        answered=len(members) - len(missing),
        total=len(members),
        missing=missing,
    )


def shift_week(record: ShiftRecord) -> date:
    return week_start(record.starts_at.astimezone(OSLO).date())


def build_month_view(
    *,
    first: date,
    today: date,
    now: datetime,
    group_names: dict[int, str],
    records: list[ShiftRecord],
    wishes: dict[int, str],
    statuses: dict[tuple[int, date], str],
    mine: list[tuple[ShiftRecord, str]],
) -> ShiftWishCalendarResponse:
    """The month as the app draws it. ``records`` are the person's member groups' shifts in the month."""
    shifts: list[WishCalendarShift] = []
    answered: set[int] = set()
    for record in records:
        day = record.starts_at.astimezone(OSLO).date()
        week = shift_week(record)
        status = effective_week_status(statuses.get((record.group_id, week)), week, today)
        past = record.starts_at <= now
        answer = wishes.get(record.shift_id)
        if answer is not None:
            answered.add(record.group_id)
        shifts.append(
            WishCalendarShift(
                shift_id=record.shift_id,
                group_id=record.group_id,
                group_name=record.group_name,
                day=day,
                starts_at=record.starts_at,
                ends_at=record.ends_at,
                time_label=format_time_range(record.starts_at, record.ends_at),
                title=record.title,
                answer=answer,  # type: ignore[arg-type]
                accepts_wishes=status in WISHES_ACCEPTED and not past,
                past=past,
            )
        )
    shifts.sort(key=lambda item: (item.starts_at, item.shift_id))
    groups = [
        WishCalendarGroup(
            group_id=group_id,
            group_name=group_names[group_id],
            has_answered=group_id in answered,
            deadline=None,
            days_left=None,
        )
        for group_id in sorted({item.group_id for item in shifts}, key=lambda gid: (group_names[gid], gid))
    ]
    return ShiftWishCalendarResponse(
        month=month_key(first),
        month_label=month_label(first),
        months=[WishMonthOption(month=month_key(m), label=month_label(m)) for m in month_options(today)],
        groups=groups,
        note=None,
        max_shifts=None,
        shifts=shifts,
        mine=[
            WishCalendarMine(
                shift_id=record.shift_id,
                kind=kind,
                day=record.starts_at.astimezone(OSLO).date(),
            )
            for record, kind in sorted(mine, key=lambda item: (item[0].starts_at, item[0].shift_id))
        ],
    )
