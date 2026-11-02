"""Loads what the leader's week page needs (``GET /shifts/week``) and hands it to ``week_view``.

It reuses ``ShiftsService.get_week_plan`` for the shifts, the placed people, the bar's pool
(with each person's place tokens), the week status and the unpublished-week banners. The
repository is read for the rest: the pool's answers per shift, shifts per person and day in every
group, shifts this month, who has begun answering this week and the statuses of the month's
weeks. Every read is one query. Scope is checked on the group before anything is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.domain.shifts.app_rules import effective_answer, effective_week_status
from app.domain.shifts.errors import ShiftForbiddenError
from app.domain.shifts.models import ShiftGroupOption, WeekPlan, WeekStatusView
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.planning import OSLO
from app.domain.shifts.week_status import leader_status
from app.domain.shifts.week_view import MemberInput, WeekInput, WeekView, build_week_view, month_weeks
from app.domain.shifts.wish_calendar import month_bounds, week_month


@dataclass(slots=True)
class WeekPage:
    """``bars`` are the tabs (only bars in the leader's scope). ``view`` is None without a bar."""

    bars: list[ShiftGroupOption]
    selected: ShiftGroupOption | None = None
    plan: WeekPlan | None = None
    view: WeekView | None = None
    status: WeekStatusView | None = None


def _bounds(first_day: date, days: int) -> tuple[datetime, datetime]:
    return (
        datetime.combine(first_day, time.min, tzinfo=OSLO),
        datetime.combine(first_day + timedelta(days=days), time.min, tzinfo=OSLO),
    )


async def load_week_page(
    service,
    *,
    week: date,
    group_id: int | None,
    scope: GroupScope,
    today: date | None = None,
) -> WeekPage:
    """``service`` is a ``ShiftsService``; only ``get_week_plan`` and ``repository`` are used."""
    repository = service.repository
    if group_id is not None and not scope.allows(group_id):
        raise ShiftForbiddenError("Du har ikke tilgang til å se skiftplanen for denne gruppa.")
    bars = [bar for bar in await repository.list_shift_groups() if scope.allows(bar.group_id)]
    if not bars:
        return WeekPage(bars=[])
    selected = next((bar for bar in bars if bar.group_id == group_id), bars[0])
    plan = await service.get_week_plan(week, selected.group_id, scope=scope)
    week_start = plan.week_start
    today = today or datetime.now(OSLO).date()

    members = [
        MemberInput(person.volunteer_id, person.name, tuple(person.allowed_tokens)) for person in plan.pool
    ]
    member_ids = [member.volunteer_id for member in members]
    shift_ids = sorted({shift.shift_id for day in plan.days for venue in day.venues for shift in venue.shifts})
    week_from, week_before = _bounds(week_start, 7)
    month = week_month(week_start)
    month_from, month_before = month_bounds(month)
    mondays = month_weeks(month)

    wishes = await repository.list_wishes_for(shift_ids, member_ids)
    answers = {
        key: answer for key, wish in wishes.items() if (answer := effective_answer(wish)) is not None
    }
    shifts_per_day: dict[tuple[int, date], int] = {}
    for record in await repository.list_shifts_between(week_from, week_before):
        day = record.starts_at.astimezone(OSLO).date()
        for volunteer_id in {person.volunteer_id for slot in record.slots for person in slot.people}:
            shifts_per_day[(volunteer_id, day)] = shifts_per_day.get((volunteer_id, day), 0) + 1
    month_counts = await repository.count_shifts_by_volunteer(month_from, month_before)
    answered = await repository.list_answered_volunteers([selected.group_id], week_from, week_before)
    stored = await repository.get_week_statuses([selected.group_id], mondays)
    strip_statuses = {
        monday: leader_status(effective_week_status(stored.get((selected.group_id, monday)), monday, today))
        for monday in mondays
    }
    status = next((item for item in plan.statuses if item.group_id == selected.group_id), None)

    view = build_week_view(
        WeekInput(
            bar_id=selected.group_id,
            bar_name=selected.name,
            week_start=week_start,
            days=plan.days,
            members=members,
            answers=answers,
            shifts_per_day=shifts_per_day,
            month_counts=month_counts,
            began=frozenset(answered.get(selected.group_id, set())) & frozenset(member_ids),
            status=status.status if status else leader_status(strip_statuses.get(week_start, "open")),
            strip_statuses=strip_statuses,
            approved_skiftleder_token=f"{selected.group_id}:skiftleder:0",
        )
    )
    return WeekPage(bars=bars, selected=selected, plan=plan, view=view, status=status)

