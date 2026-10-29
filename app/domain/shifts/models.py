from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time


@dataclass(slots=True)
class AssignedPerson:
    assignment_id: int
    volunteer_id: int
    name: str
    swap_requested: bool
    # "Kan ikke i dag": still listed, but the place counts as open.
    cannot_attend: bool = False
    first_name: str = ""
    # Soft rule hints for the leader board, filled in when the board is built.
    warnings: list[str] = field(default_factory=list)


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
    group_id: int
    open_for_samf: bool
    open_label: str


@dataclass(slots=True)
class ShiftWishes:
    """Who answered what for one shift, names only for the leader's pool."""

    vil: list[str] = field(default_factory=list)
    kan: list[str] = field(default_factory=list)
    nei: list[str] = field(default_factory=list)
    ikke_satt: list[str] = field(default_factory=list)


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
    people_count: int = 0
    wishes: ShiftWishes | None = None


@dataclass(slots=True)
class PoolPerson:
    volunteer_id: int
    name: str
    group_name: str
    role_name: str
    shifts_this_week: int
    # "group_id:kind:open" tokens for places this person can take; the page
    # uses them to highlight drop targets, the server checks again on save.
    allowed_tokens: list[str]


@dataclass(slots=True)
class MemberRole:
    volunteer_id: int
    name: str
    group_id: int
    group_name: str
    role_name: str | None


@dataclass(slots=True)
class AssignmentRef:
    assignment_id: int
    slot_id: int
    shift_id: int
    volunteer_id: int


@dataclass(slots=True)
class PlacementResult:
    message: str


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
class WeekStatusView:
    group_id: int
    group_name: str
    status: str
    label: str
    explicit: bool
    is_past: bool
    can_open: bool = True


@dataclass(slots=True)
class WeekBanner:
    group_id: int
    group_name: str
    week_start: date
    text: str
    urgent: bool


@dataclass(slots=True)
class GroupSettingsView:
    group_id: int
    name: str
    is_subgroup: bool
    app_enabled: bool
    samf_enabled: bool
    meeting_point: str
    courses_enabled: bool = False
    # Names of required courses (Brannkurs, Barkurs) not found in Personal, only
    # filled in when courses_enabled is on.
    missing_courses: list[str] = field(default_factory=list)


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
    pool: list[PoolPerson] = field(default_factory=list)
    statuses: list[WeekStatusView] = field(default_factory=list)
    banners: list[WeekBanner] = field(default_factory=list)


@dataclass(slots=True)
class ClosedPeriod:
    period_id: int
    group_id: int
    starts_on: date
    ends_on: date
    label: str
    group_name: str = ""


@dataclass(slots=True)
class ClosedPeriodSection:
    """One group's closed periods: its own (editable) and those inherited from ancestors."""

    group_id: int
    label: str
    own: list[ClosedPeriod] = field(default_factory=list)
    inherited: list[ClosedPeriod] = field(default_factory=list)


@dataclass(slots=True)
class ClosedShift:
    """A shift the generator skipped, and the closed period (maybe an ancestor's) behind it."""

    shift: PlannedShift
    period: ClosedPeriod
    inherited: bool


@dataclass(slots=True)
class OtherGroup:
    group_id: int
    name: str


@dataclass(slots=True)
class TemplateSlot:
    kind: str
    count: int
    open_for_samf: bool
    sort_order: int


@dataclass(slots=True)
class ShiftTemplate:
    template_id: int
    group_id: int
    group_name: str
    venue: str
    title: str
    weekday: int
    start_time: time
    end_time: time
    training_replaces_slot: bool
    is_active: bool
    slots: list[TemplateSlot] = field(default_factory=list)


@dataclass(slots=True)
class PlannedShift:
    template_id: int
    group_id: int
    venue: str
    title: str
    day: date
    starts_at: datetime
    ends_at: datetime
    training_replaces_slot: bool
    slots: list[TemplateSlot]


@dataclass(slots=True)
class GenerationPreview:
    group_id: int
    group_name: str
    date_from: date
    date_to: date
    to_create: list[PlannedShift] = field(default_factory=list)
    existing: list[PlannedShift] = field(default_factory=list)
    closed: list[ClosedShift] = field(default_factory=list)
