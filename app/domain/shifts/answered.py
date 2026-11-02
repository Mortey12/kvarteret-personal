"""Board side of "Har svart N av M": who has answered per bar and month.

Counts come from ``shift_wishes`` only (a note alone does not count). Only people in the
leader's pool are read, so a leader never gets names outside their scope.
"""

from __future__ import annotations

from datetime import date

from app.domain.shifts.models import AnsweredView, ShiftGroupOption
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.wish_calendar import answered_summary, month_bounds, week_month


async def answered_views(
    repository: ShiftsRepository,
    *,
    semester: int,
    week_start: date,
    bars: list[ShiftGroupOption],
    pool_ids: list[int],
) -> list[AnsweredView]:
    """One entry per bar, for the month the displayed week counts for.

    M for a bar is the people who are members of that bar this semester and in the leader's
    pool (each person once). N is those of them with at least one answer on a shift of that
    bar in the month.
    """
    if not bars or not pool_ids:
        return []
    month = week_month(week_start)
    starts_from, starts_before = month_bounds(month)
    bar_ids = [bar.group_id for bar in bars]
    members: dict[int, dict[int, str]] = {gid: {} for gid in bar_ids}
    for member in await repository.list_shift_members(semester, sorted(set(pool_ids) & set(bar_ids))):
        if member.group_id in members:
            members[member.group_id].setdefault(member.volunteer_id, member.name)
    answered = await repository.list_answered_volunteers(bar_ids, starts_from, starts_before)
    return [
        answered_summary(
            group_id=bar.group_id,
            group_name=bar.name,
            month=month,
            members=members[bar.group_id],
            answered=answered.get(bar.group_id, set()),
        )
        for bar in bars
    ]
