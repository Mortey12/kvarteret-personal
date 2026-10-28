"""Turning shift templates into dated shifts (pure rules)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from app.domain.shifts.models import (
    ClosedPeriod,
    ClosedShift,
    GenerationPreview,
    PlannedShift,
    ShiftTemplate,
)
from app.domain.shifts.planning import OSLO

MAX_RANGE_DAYS = 400


def shift_span(template: ShiftTemplate, day: date) -> tuple[datetime, datetime]:
    """Start and end in UTC. An end time at or before the start is after midnight."""
    starts_at = datetime.combine(day, template.start_time, tzinfo=OSLO)
    end_day = day + timedelta(days=1) if template.end_time <= template.start_time else day
    ends_at = datetime.combine(end_day, template.end_time, tzinfo=OSLO)
    return starts_at.astimezone(UTC), ends_at.astimezone(UTC)


def closed_period_on(day: date, periods: Iterable[ClosedPeriod]) -> ClosedPeriod | None:
    """The period that covers ``day``; both ends are included."""
    return next((p for p in periods if p.starts_on <= day <= p.ends_on), None)


def plan_generation(
    *,
    group_id: int,
    group_name: str,
    templates: Iterable[ShiftTemplate],
    periods: Iterable[ClosedPeriod],
    existing: set[tuple[int, date]],
    date_from: date,
    date_to: date,
) -> GenerationPreview:
    """What a run would do: create, skip because it exists, or skip because closed.

    ``periods`` are the group's own closed periods and those of its ancestors.
    ``existing`` holds (template id, Oslo date) of shifts already made, so a template whose
    time was edited does not get a second shift on a day that already has one.
    Inactive templates give nothing.
    """
    periods = list(periods)
    active = [t for t in templates if t.is_active and t.group_id == group_id]
    preview = GenerationPreview(group_id, group_name, date_from, date_to)
    day = date_from
    while day <= date_to:
        for template in active:
            if template.weekday != day.weekday():
                continue
            starts_at, ends_at = shift_span(template, day)
            planned = PlannedShift(
                template_id=template.template_id,
                group_id=group_id,
                venue=template.venue,
                title=template.title,
                day=day,
                starts_at=starts_at,
                ends_at=ends_at,
                training_replaces_slot=template.training_replaces_slot,
                slots=list(template.slots),
            )
            if (template.template_id, day) in existing:
                preview.existing.append(planned)
            elif period := closed_period_on(day, periods):
                preview.closed.append(
                    ClosedShift(planned, period, inherited=period.group_id != group_id)
                )
            else:
                preview.to_create.append(planned)
        day += timedelta(days=1)
    for bucket in (preview.to_create, preview.existing):
        bucket.sort(key=lambda p: (p.starts_at, p.template_id))
    preview.closed.sort(key=lambda item: (item.shift.starts_at, item.shift.template_id))
    return preview
