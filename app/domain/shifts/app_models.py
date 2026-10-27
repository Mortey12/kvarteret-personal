"""Response and request shapes for Skiftlista in the app (kvarteret-internbevis).

The app ships once to the stores, so everything it shows (tabs, labels,
which groups are on) comes from these responses. Keep changes additive:
installed app versions keep parsing old fields after a backend deploy.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WishAnswer = Literal["vil", "kan", "nei"]
Tone = Literal["ok", "info", "warn", "off"]
RequirementState = Literal["ok", "warn", "block"]
Audience = Literal["skjenke", "samfunnettjeneste", "none"]
OpenScope = Literal["own", "other", "all"]
PeriodStatus = Literal["open", "allocating", "published"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- config


class ShiftAppTab(_Model):
    id: Literal["wishes", "open", "mine"]
    label: str
    # For "open" tabs: which shifts the tab lists (pass as ?scope=).
    scope: OpenScope | None = None


class ShiftAppGroup(_Model):
    group_id: int
    name: str
    is_member: bool
    # The person's verv in the group this semester ("Skiftleder", "Medlem", ...).
    role: str | None
    in_training: bool
    training_done: int | None
    training_needed: int | None
    meeting_point: str | None
    quota_text: str | None


class ShiftSlotKind(_Model):
    kind: str
    label: str
    crew: bool
    # Needs a verv (skiftleder, kokk, opplæring): never open to others.
    restricted: bool
    description: str | None


class LabeledValue(_Model):
    value: str
    label: str


class ShiftAppConfigResponse(_Model):
    enabled: bool
    audience: Audience
    tabs: list[ShiftAppTab]
    groups: list[ShiftAppGroup]
    slot_kinds: list[ShiftSlotKind]
    wish_answers: list[LabeledValue]
    # Index 0 = Monday, like weekday numbers elsewhere in this API.
    weekdays: list[str]
    courses: list[LabeledValue]
    samf_target: int


# ---------------------------------------------------------------- shifts


class ShiftSummary(_Model):
    shift_id: int
    group_id: int
    group_name: str
    venue: str
    venue_label: str
    title: str
    starts_at: datetime
    ends_at: datetime
    day: date
    day_label: str
    time_label: str
    note: str | None
    # Only the age limit ("20+") is ever shown, never a birth date.
    min_age: int | None


class CourseOffer(_Model):
    course_key: str
    course_name: str
    session_id: int
    starts_at: datetime
    label: str


class OpenPlace(_Model):
    slot_id: int
    kind: str
    label: str
    open_places: int
    opened_by_absence: bool
    counts_as_samfunnettjeneste: bool
    tone: Tone
    status_text: str
    can_claim: bool
    # Sign up for these course sessions in the same claim to take the place.
    needs_course_signup: list[CourseOffer]


class OpenShift(ShiftSummary):
    places: list[OpenPlace]


class OpenShiftsResponse(_Model):
    scope: OpenScope
    from_day: date
    to_day: date
    shifts: list[OpenShift]
    # True when there are places after to_day, within the longest window.
    has_more: bool = False


class Requirement(_Model):
    text: str
    state: RequirementState
    course_key: str | None = None


class CrewMember(_Model):
    first_name: str
    slot_label: str


class PlaceDetailResponse(_Model):
    shift: ShiftSummary
    place: OpenPlace
    description: str | None
    meeting_point: str | None
    requirements: list[Requirement]
    crew: list[CrewMember]
    claim_label: str | None


class ClaimPlaceRequest(_Model):
    course_session_ids: list[int] = Field(default_factory=list, max_length=4)


class MyShift(ShiftSummary):
    assignment_id: int
    slot_id: int
    kind: str
    label: str
    tag: str | None
    counts_as_samfunnettjeneste: bool
    swap_requested: bool
    cannot_attend: bool
    can_request_swap: bool
    can_report_cannot_attend: bool
    meeting_point: str | None
    description: str | None
    crew: list[CrewMember]
    # Same rule as the leader board: more than one shift in the week.
    warnings: list[str] = Field(default_factory=list)


class QuotaSummary(_Model):
    label: str
    taken: int
    limit: int
    text: str


class SamfProgress(_Model):
    done: int
    planned: int
    target: int
    text: str


class MyShiftsResponse(_Model):
    shifts: list[MyShift]
    quota: QuotaSummary | None
    samfunnettjeneste: SamfProgress | None


class ShiftActionResponse(_Model):
    message: str
    shift: MyShift


class StatusMessageRequest(_Model):
    # Optional and only seen by Skjenkeleder and the leader/personalansvarlig
    # of the bar or kitchen. Deleted after the shift.
    message: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------- wishes


class WeekdayRule(_Model):
    weekday: int
    label: str
    answer: WishAnswer | None


class WishWeek(_Model):
    group_id: int
    group_name: str
    week_start: date
    week_number: int
    label: str
    status: PeriodStatus
    status_label: str
    note: str
    accepts_wishes: bool


class WishShift(ShiftSummary):
    answer: WishAnswer | None
    from_rule: bool


class ShiftWishesResponse(_Model):
    semester: int
    semester_label: str
    rules: list[WeekdayRule]
    weeks: list[WishWeek]
    shifts: list[WishShift]


class WishAnswerRequest(_Model):
    answer: WishAnswer | None


# ---------------------------------------------------------------- courses


class CourseSessionOut(_Model):
    session_id: int
    starts_at: datetime
    label: str
    location: str | None
    spots_left: int
    signed_up: bool


class CourseStatus(_Model):
    key: str
    name: str
    valid: bool
    status_text: str
    valid_through: str | None
    expires_soon: bool
    signed_up: CourseSessionOut | None
    sessions: list[CourseSessionOut]


class ShiftCoursesResponse(_Model):
    courses: list[CourseStatus]
