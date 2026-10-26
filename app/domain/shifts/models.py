from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(slots=True)
class AssignedPerson:
    assignment_id: int
    volunteer_id: int
    name: str
    swap_requested: bool


@dataclass(slots=True)
class SlotRecord:
    slot_id: int
    kind: str
    count: int
    open_for_samf: bool
    sort_order: int
    people: list[AssignedPerson] = field(default_factory=list)


@dataclass(slots=True)
class ShiftRecord:
    shift_id: int
    group_id: int
    group_name: str
    venue: str
    title: str
    starts_at: datetime
    ends_at: datetime
    note: str | None
    is_closed: bool
    training_replaces_slot: bool
    slots: list[SlotRecord] = field(default_factory=list)


@dataclass(slots=True)
class ShiftGroupOption:
    group_id: int
    name: str


@dataclass(slots=True)
class SlotView:
    slot_id: int
    kind: str
    label: str
    count: int
    filled_text: str
    tag: str
    people: list[AssignedPerson]
    open_places: int
    training_places: int
    blocked_by_training: bool


@dataclass(slots=True)
class ShiftView:
    shift_id: int
    group_name: str
    title: str
    time_label: str
    note: str | None
    is_closed: bool
    training_replaces_slot: bool
    open_places: int
    slots: list[SlotView]


@dataclass(slots=True)
class VenueBlock:
    label: str
    shifts: list[ShiftView]


@dataclass(slots=True)
class DayBlock:
    day: date
    label: str
    venues: list[VenueBlock]


@dataclass(slots=True)
class WeekPlan:
    week_start: date
    week_label: str
    previous_week: date
    next_week: date
    selected_group_id: int | None
    groups: list[ShiftGroupOption]
    days: list[DayBlock]
    open_places: int
    swap_requests: int
