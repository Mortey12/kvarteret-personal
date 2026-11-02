"""Weekday rules and shift wishes."""

from __future__ import annotations

from datetime import date, timedelta

from app.domain.shifts.app_models import (
    ShiftWishesResponse,
    WeekdayRule,
    WishWeek,
    WishShift,
)
from app.domain.shifts.app_rules import (
    PERIOD_STATUSES,
    STATUS_PUBLISHED,
    WISHES_ACCEPTED,
    effective_week_status,
    oslo_midnight,
    semester_label,
    week_label,
    week_start,
    effective_answer,
    wish_window_end,
)
from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import ShiftRecord
from app.domain.shifts.planning import (
    OSLO,
    WEEKDAYS_NO,
)


class WishesMixin:
    # ------------------------------------------------------------ wishes

    async def get_wishes(self, volunteer_id: int) -> ShiftWishesResponse:
        person = await self._require_wishes(volunteer_id)
        now = self._now()
        group_ids = person.member_enabled_ids
        weekdays = await self.repository.list_template_weekdays(group_ids)
        rules = await self.repository.list_rules(person.volunteer_id, person.semester)
        today = now.astimezone(OSLO).date()
        records = await self.repository.list_shifts_between(
            now, oslo_midnight(wish_window_end(today) + timedelta(days=1)), group_ids=sorted(group_ids)
        )
        by_week: dict[tuple[int, date], list[ShiftRecord]] = {}
        for record in records:
            week = week_start(record.starts_at.astimezone(OSLO).date())
            by_week.setdefault((record.group_id, week), []).append(record)
        statuses = await self.repository.week_statuses(
            sorted(group_ids), sorted({week for _, week in by_week})
        )

        week_rows: list[WishWeek] = []
        accepting: set[tuple[int, date]] = set()
        for group_id, week in sorted(by_week, key=lambda key: (key[1], person.group_names[key[0]])):
            status = effective_week_status(statuses.get((group_id, week)), week, today)
            label, note = PERIOD_STATUSES.get(status, PERIOD_STATUSES[STATUS_PUBLISHED])
            accepts = status in WISHES_ACCEPTED
            if accepts:
                accepting.add((group_id, week))
            week_rows.append(
                WishWeek(
                    group_id=group_id,
                    group_name=person.group_names[group_id],
                    week_start=week,
                    week_number=week.isocalendar().week,
                    label=week_label(week),
                    status=status,  # type: ignore[arg-type]
                    status_label=label,
                    note=note,
                    accepts_wishes=accepts,
                )
            )

        shift_rows: list[WishShift] = []
        records = [
            record
            for record in records
            if (record.group_id, week_start(record.starts_at.astimezone(OSLO).date())) in accepting
        ]
        if records:
            wishes = await self.repository.list_wishes(
                person.volunteer_id, [record.shift_id for record in records]
            )
            for record in records:
                weekday = record.starts_at.astimezone(OSLO).weekday()
                explicit = wishes.get(record.shift_id)
                shift_rows.append(
                    WishShift(
                        **self._summary(record).model_dump(),
                        answer=effective_answer(explicit, rules.get(weekday)),  # type: ignore[arg-type]
                        from_rule=explicit is None,
                    )
                )
        return ShiftWishesResponse(
            semester=person.semester,
            semester_label=semester_label(person.semester).capitalize(),
            rules=[
                WeekdayRule(weekday=day, label=WEEKDAYS_NO[day], answer=rules.get(day))  # type: ignore[arg-type]
                for day in sorted(weekdays | set(rules))
            ],
            weeks=week_rows,
            shifts=shift_rows,
        )

    async def set_weekday_rule(self, volunteer_id: int, weekday: int, answer: str | None) -> ShiftWishesResponse:
        person = await self._require_wishes(volunteer_id)
        await self.repository.set_rule(person.volunteer_id, person.semester, weekday, answer)
        return await self.get_wishes(volunteer_id)

    async def set_shift_wish(self, volunteer_id: int, shift_id: int, answer: str | None) -> ShiftWishesResponse:
        person = await self._require_wishes(volunteer_id)
        record = await self.repository.get_shift(shift_id)
        if record is None or record.group_id not in person.member_enabled_ids:
            raise ShiftNotFoundError("Finner ikke skiftet.")
        if record.starts_at <= self._now():
            raise ShiftPlacementError("Skiftet har allerede vært.")
        week = week_start(record.starts_at.astimezone(OSLO).date())
        statuses = await self.repository.week_statuses([record.group_id], [week])
        today = self._now().astimezone(OSLO).date()
        if effective_week_status(statuses.get((record.group_id, week)), week, today) not in WISHES_ACCEPTED:
            raise ShiftPlacementError("Planen for denne uka er publisert. Be om bytte i stedet.")
        await self.repository.set_wish(person.volunteer_id, shift_id, answer)
        return await self.get_wishes(volunteer_id)
