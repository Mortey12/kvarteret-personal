"""Shift wishes: the list, the month calendar and the batch call."""

from __future__ import annotations

from datetime import date, timedelta

from app.domain.shifts.app_models import (
    ShiftWishCalendarResponse,
    ShiftWishesResponse,
    WishAnswerItem,
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
from app.domain.shifts.app_common import logger
from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import ShiftRecord
from app.domain.shifts.planning import OSLO
from app.domain.shifts.wish_calendar import (
    build_month_view,
    month_bounds,
    month_of,
    month_options,
    parse_month,
    refusal_reason,
    refusal_text,
    shift_week,
)


class WishesMixin:
    # ------------------------------------------------------------ wishes

    async def get_wishes(self, volunteer_id: int) -> ShiftWishesResponse:
        person = await self._require_wishes(volunteer_id)
        now = self._now()
        group_ids = person.member_enabled_ids
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
                shift_rows.append(
                    WishShift(
                        **self._summary(record).model_dump(),
                        answer=effective_answer(wishes.get(record.shift_id)),  # type: ignore[arg-type]
                        from_rule=False,
                    )
                )
        return ShiftWishesResponse(
            semester=person.semester,
            semester_label=semester_label(person.semester).capitalize(),
            rules=[],  # weekday rules are retired; the field stays for installed builds
            weeks=week_rows,
            shifts=shift_rows,
        )

    async def set_weekday_rule(self, volunteer_id: int, weekday: int, answer: str | None) -> ShiftWishesResponse:
        """Deprecated: stored, but nothing reads it."""
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
        self._log_wishes(person.volunteer_id, shift_id, count=1)
        return await self.get_wishes(volunteer_id)

    # ------------------------------------------------------------ calendar

    async def get_wish_calendar(self, volunteer_id: int, month: str | None = None) -> ShiftWishCalendarResponse:
        person = await self._require_wishes(volunteer_id)
        today = self._now().astimezone(OSLO).date()
        options = month_options(today)
        first = date(today.year, today.month, 1) if month is None else parse_month(month)
        if first is None or first not in options:
            raise ShiftNotFoundError("Finner ikke måneden.")
        return await self._month_view(person, first)

    async def set_shift_wish_answers(
        self, volunteer_id: int, items: list[WishAnswerItem]
    ) -> ShiftWishCalendarResponse:
        """All or nothing: check every item, then write once. Same rules as ``set_shift_wish``."""
        person = await self._require_wishes(volunteer_id)
        now = self._now()
        today = now.astimezone(OSLO).date()
        ids = [item.shift_id for item in items]
        found = {r.shift_id: r for r in await self.repository.list_shifts_by_ids(ids)}
        visible = {sid: r for sid, r in found.items() if r.group_id in person.member_enabled_ids}
        statuses = await self.repository.week_statuses(
            sorted({r.group_id for r in visible.values()}), sorted({shift_week(r) for r in visible.values()})
        )
        reasons: list[str] = []
        for item in items:
            record = visible.get(item.shift_id)
            status = ""
            if record is not None:
                week = shift_week(record)
                status = effective_week_status(statuses.get((record.group_id, week)), week, today)
            reason = refusal_reason(
                visible=record is not None,
                started=record is not None and record.starts_at <= now,
                status=status,
            )
            if reason is not None:
                reasons.append(reason)
        if reasons:
            raise ShiftPlacementError(refusal_text(reasons, len(items)))
        await self.repository.replace_wishes(person.volunteer_id, {item.shift_id: item.answer for item in items})
        self._log_wishes(person.volunteer_id, ids[0], count=len(items))
        return await self._month_view(person, month_of(visible[ids[0]].starts_at))

    async def _month_view(self, person, first: date) -> ShiftWishCalendarResponse:
        now = self._now()
        today = now.astimezone(OSLO).date()
        start, end = month_bounds(first)
        group_ids = sorted(person.member_enabled_ids)
        records = await self.repository.list_shifts_between(start, end, group_ids=group_ids)
        wishes = await self.repository.list_wishes(person.volunteer_id, [r.shift_id for r in records])
        statuses = await self.repository.week_statuses(group_ids, sorted({shift_week(r) for r in records}))
        own = await self.repository.list_volunteer_shifts(person.volunteer_id, start, end)
        published = {r.shift_id for r in await self._published_only(own)}
        mine = [
            (record, slot.kind)
            for record in own
            if record.shift_id in published
            for slot in record.slots
            if any(p.volunteer_id == person.volunteer_id for p in slot.people)
        ]
        return build_month_view(
            first=first,
            today=today,
            now=now,
            group_names=person.group_names,
            records=records,
            wishes=wishes,
            statuses=statuses,
            mine=mine,
        )

    @staticmethod
    def _log_wishes(volunteer_id: int, subject_id: int, *, count: int) -> None:
        # The number of answers only, never the answers themselves.
        logger.info(
            "shift.app.wishes",
            extra={
                "event": "shift.app.wishes",
                "volunteer_id": volunteer_id,
                "subject_type": "shift",
                "subject_id": subject_id,
                "count": count,
            },
        )
