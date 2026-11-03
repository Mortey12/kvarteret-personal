"""Response shapes for the Skiftlista leader app (a separate web frontend).

The leader app only shows what these say; every rule (who fits a place, what to
warn about) is decided here on the server. Names and shapes follow the types the
frontend was built against (``apps/skiftlista/src/core/types.ts`` in the
samfunnetibergen repository), with two differences that come from how Personal
stores places: a place has a ``seat`` (a slot with room for several people has
one slot id for all of them), and the shown week status is only ``open`` or
``published`` (Personal has no "changed after publishing" state).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Answer = Literal["vil", "kan", "nei"]
AnswerStatus = Literal["ikke_svart", "paabegynt", "ferdig"]
WeekStatus = Literal["open", "published"]
Level = Literal["red", "yellow"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeaderBar(_Model):
    id: int
    name: str


class LeaderMe(_Model):
    name: str
    role: Literal["Admin", "Gruppeadmin"]
    # Personal's CSRF token, sent back as X-CSRF-Token on every write.
    csrf_token: str
    bars: list[LeaderBar]


class Note(_Model):
    text: str
    level: Level


class Assignment(_Model):
    assignment_id: int
    volunteer_id: int
    cannot_attend: bool
    swap_requested: bool
    # "opplæring 2/3" once the training counter exists, else null.
    training: str | None = None
    # The person's problems on this place, worded by the server.
    problems: list[Note]


class Suggestion(_Model):
    volunteer_id: int
    why: str


class Place(_Model):
    slot_id: int
    # 0-based position among the places of the same slot on the same shift.
    seat: int
    kind: str
    # Null for an empty place. A person who said "kan ikke i dag" is still here,
    # with ``cannot_attend`` true.
    assignment: Assignment | None = None
    suggestion: Suggestion | None = None
    # True when this place still has to be filled for the shift to be staffed: it is empty (or
    # the person said "kan ikke i dag"), the shift is not closed, and it is not an Opplæring
    # place (a trainee is an extra, never a need). The "ledige" count is the number of these.
    needed: bool = False


class Shift(_Model):
    shift_id: int
    # Oslo date, ISO yyyy-mm-dd.
    day: str
    # 1-based position in the bar's day.
    position: int
    title: str
    # "16:30–22:00", formatted by the server.
    time_label: str
    # Real times in Oslo time with offset, e.g. "2026-11-02T11:00:00+01:00". A shift past
    # midnight ends on the next day. Null if the shift's times are not known.
    starts_at: str | None = None
    ends_at: str | None = None
    # Planned length in minutes (ends_at minus starts_at, so clock changes are right).
    duration_minutes: int | None = None
    closed: bool
    places: list[Place]
    # volunteer id (as text) -> answer, only for people in the leader's pool.
    answers: dict[str, Answer]


class Person(_Model):
    volunteer_id: int
    name: str
    is_skiftleder: bool
    week_shifts: int
    month_shifts: int
    # Planned minutes this week on shifts the person is coming to, in every group.
    week_minutes: int
    answer_status: AnswerStatus
    # ISO days this week with a shift in any group.
    busy_days: list[str]
    note: str | None = None
    max_shifts: int | None = None
    tags: list[str]


class FitOk(_Model):
    ok: Literal[True]
    warnings: list[Note]
    # True when the person lacks what the place needs. The leader may still place them,
    # but the app must show the red warning and ask for a confirmation first.
    needs_confirmation: bool = False


class FitRefused(_Model):
    ok: Literal[False]
    reason: str


class WeekRow(_Model):
    position: int
    label: str
    time_label: str


class WeekStripItem(_Model):
    week_start: str
    number: int
    status: WeekStatus


class LastChanged(_Model):
    by: str
    at: str


class WeekBoard(_Model):
    bar: LeaderBar
    week_start: str
    week_number: int
    # Changes whenever anything shown changes; later sent back as If-Match.
    version: str
    status: WeekStatus
    deadline: str | None = None
    rows: list[WeekRow]
    shifts: list[Shift]
    people: list[Person]
    # volunteer id (as text) -> slot id (as text) -> fit. Only open places.
    fit: dict[str, dict[str, FitOk | FitRefused]]
    strip: list[WeekStripItem]
    last_changed: LastChanged | None = None


# ---- writes -----------------------------------------------------------------------------------
# Every write carries `group_id` and `week_start` as query parameters (which bar and week the
# leader is looking at) and the board's `version` as the `If-Match` header. A stale version is a
# 409; so is any rule that refuses the change. The answer is `WriteResult`.


class PlaceRequest(_Model):
    slot_id: int
    volunteer_id: int
    # The leader accepted the big warning for a person who lacks the qualification or membership.
    confirmed: bool = False


class AddPlaceRequest(_Model):
    # One of Personal's place kinds: skiftleder, skjenker, bartender, kokk, opplaering, garderobe.
    kind: str


class ReplaceRequest(_Model):
    # The place the page showed, so a place that changed meanwhile is refused.
    slot_id: int
    volunteer_id: int
    confirmed: bool = False


class MoveRequest(_Model):
    slot_id: int
    confirmed: bool = False


class SwapRequest(_Model):
    assignment_a: int
    assignment_b: int
    confirmed: bool = False


class StatusRequest(_Model):
    status: Literal["open", "published"]


class WriteResult(_Model):
    message: str
    # The placed person's problems on the refreshed board, worded by the server.
    warnings: list[Note]
    board: WeekBoard
