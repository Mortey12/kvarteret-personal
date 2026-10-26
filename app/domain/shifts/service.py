from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Protocol

from app.domain.shifts.models import WeekPlan
from app.domain.shifts.planning import OSLO, format_week, group_into_days, monday_of
from app.domain.shifts.repository import ShiftsRepository


class ShiftsServiceProtocol(Protocol):
    async def get_week_plan(self, week: date, group_id: int | None = None) -> WeekPlan: ...


class ShiftsService:
    def __init__(self, repository: ShiftsRepository) -> None:
        self.repository = repository

    async def get_week_plan(self, week: date, group_id: int | None = None) -> WeekPlan:
        week_start = monday_of(week)
        starts_from = datetime.combine(week_start, time.min, tzinfo=OSLO)
        starts_before = datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=OSLO)
        group_options = await self.repository.list_shift_groups()
        if group_id is not None and all(option.group_id != group_id for option in group_options):
            group_id = None
        records = await self.repository.list_shifts_between(
            starts_from, starts_before, group_id=group_id
        )
        days = group_into_days(records)
        open_places = sum(
            shift.open_places for day in days for venue in day.venues for shift in venue.shifts
        )
        swap_requests = sum(
            1
            for record in records
            for slot in record.slots
            for person in slot.people
            if person.swap_requested
        )
        return WeekPlan(
            week_start=week_start,
            week_label=format_week(week_start),
            previous_week=week_start - timedelta(days=7),
            next_week=week_start + timedelta(days=7),
            selected_group_id=group_id,
            groups=group_options,
            days=days,
            open_places=open_places,
            swap_requests=swap_requests,
        )
