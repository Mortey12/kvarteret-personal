"""Turns the leader week view into the JSON board the Skiftlista leader app reads. No I/O.

This is a translation only. Who fits which place, the warnings and the problems on
a placed person all come from ``week_view`` and ``week_picker`` (the same functions
the server-rendered week page uses), so a rule exists in one place.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from app.domain.shifts.leader_models import (
    Assignment,
    FitOk,
    FitRefused,
    LeaderBar,
    Note,
    Person,
    Place,
    Shift,
    WeekBoard,
    WeekRow,
    WeekStripItem,
)
from app.domain.shifts.planning import OSLO
from app.domain.shifts.week_view import PlaceView, WeekView

_STATUSES = {"open", "published"}


def _status(value: str) -> str:
    # Leaders only know open and published here; anything else counts as open.
    return value if value in _STATUSES else "open"


def _place(place: PlaceView, seat: int, *, closed: bool) -> Place:
    assignment = None
    if place.volunteer_id is not None and place.assignment_id is not None:
        assignment = Assignment(
            assignment_id=place.assignment_id,
            volunteer_id=place.volunteer_id,
            cannot_attend=place.cannot_attend,
            swap_requested=place.swap_requested,
            problems=[Note(text=item.text, level=item.level) for item in place.problems],
        )
    empty = assignment is None or assignment.cannot_attend
    return Place(
        slot_id=place.slot_id,
        seat=seat,
        kind=place.kind,
        assignment=assignment,
        needed=empty and not closed and place.kind != "opplaering",
    )


def _one_box_per_seat(places: list[PlaceView]) -> list[PlaceView]:
    """Personal's view lists a person who said "kan ikke i dag" AND counts their seat as open, so
    that seat would be drawn twice. The person's own box stays (it is the one to replace); one
    open box per such person is dropped, which leaves exactly as many boxes as the slot has seats."""
    absent: dict[int, int] = {}
    for place in places:
        if place.cannot_attend:
            absent[place.slot_id] = absent.get(place.slot_id, 0) + 1
    kept: list[PlaceView] = []
    for place in places:
        if place.volunteer_id is None and absent.get(place.slot_id, 0) > 0:
            absent[place.slot_id] -= 1
            continue
        kept.append(place)
    return kept


def _fit(raw: dict) -> FitOk | FitRefused:
    if raw.get("ok"):
        return FitOk(
            ok=True,
            warnings=[Note(text=text, level=level) for text, level in raw.get("w", [])],
            needs_confirmation=bool(raw.get("c")),
        )
    return FitRefused(ok=False, reason=raw.get("why", ""))


def to_board(
    view: WeekView,
    *,
    times: dict[int, tuple[datetime, datetime]] | None = None,
    week_minutes: dict[int, int] | None = None,
) -> WeekBoard:
    times = times or {}
    week_minutes = week_minutes or {}
    answers_by_shift = {item["shift_id"]: item["answers"] for item in view.data["shifts"]}
    busy_by_person = {item["volunteer_id"]: item["busy_days"] for item in view.data["people"]}

    shifts: list[Shift] = []
    for row in view.rows:
        for cell in row.cells:
            if cell is None:
                continue
            seats: dict[int, int] = {}
            places: list[Place] = []
            for place in _one_box_per_seat(cell.places):
                seat = seats.get(place.slot_id, 0)
                seats[place.slot_id] = seat + 1
                places.append(_place(place, seat, closed=cell.is_closed))
            start, end = times.get(cell.shift_id, (None, None))
            shifts.append(
                Shift(
                    shift_id=cell.shift_id,
                    day=cell.day.isoformat(),
                    position=row.position,
                    title=cell.title,
                    time_label=cell.time_label,
                    starts_at=start.astimezone(OSLO).isoformat() if start else None,
                    ends_at=end.astimezone(OSLO).isoformat() if end else None,
                    duration_minutes=int((end - start).total_seconds() // 60) if start and end else None,
                    closed=cell.is_closed,
                    places=places,
                    answers=answers_by_shift.get(cell.shift_id, {}),
                )
            )

    board = WeekBoard(
        bar=LeaderBar(id=view.bar_id, name=view.bar_name),
        week_start=view.week_start.isoformat(),
        week_number=view.week_start.isocalendar().week,
        version="",
        status=_status(view.status),
        rows=[WeekRow(position=row.position, label=row.title, time_label=row.time_label) for row in view.rows],
        shifts=shifts,
        people=[
            Person(
                volunteer_id=person.volunteer_id,
                name=person.name,
                is_skiftleder=person.is_sl,
                week_shifts=person.week_count,
                month_shifts=person.month_count,
                week_minutes=week_minutes.get(person.volunteer_id, 0),
                answer_status="paabegynt" if person.began else "ikke_svart",
                busy_days=busy_by_person.get(person.volunteer_id, []),
                tags=[],
            )
            for person in view.people
        ],
        fit={
            volunteer_id: {slot_id: _fit(raw) for slot_id, raw in per_slot.items()}
            for volunteer_id, per_slot in view.data["place_state"].items()
        },
        strip=[
            WeekStripItem(week_start=item.week_start.isoformat(), number=item.number, status=_status(item.status))
            for item in view.strip
        ],
    )
    return board.model_copy(update={"version": _version(board)})


def _version(board: WeekBoard) -> str:
    """A fingerprint of everything shown: it changes when any of it changes."""
    text = json.dumps(board.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]
