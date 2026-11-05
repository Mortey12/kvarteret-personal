"""Turns the leader week view into the JSON board the Skiftlista leader app reads. No I/O.

This is a translation only. Who fits which place, the warnings and the problems on
a placed person all come from ``week_view`` and ``week_picker`` (the same functions
the server-rendered week page uses), so a rule exists in one place.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from app.domain.shifts.leader_models import (
    PersonTraining,
    Assignment,
    FitOk,
    FitRefused,
    LastChanged,
    LeaderBar,
    Note,
    Person,
    Place,
    Shift,
    Suggestion,
    WeekBoard,
    WeekRow,
    WeekStripItem,
)
from app.domain.shifts.planning import OSLO
from app.domain.shifts.training import place_text
from app.domain.shifts.week_view import PlaceView, WeekView

_STATUSES = {"open", "published"}


def _status(value: str) -> str:
    # Leaders only know open and published here; anything else counts as open.
    return value if value in _STATUSES else "open"


def _status_of(value: str, week: date, changes: dict[date, tuple[datetime, str]]) -> str:
    status = _status(value)
    return "changed_after_publish" if status == "published" and week in changes else status


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


def _is_sl_trainee(trainings: dict, volunteer_id: int) -> bool:
    return any(v.record.kind == "skiftleder" for v in trainings.get(volunteer_id, []))


def _has_experienced_skiftleder(places: list[Place], trainings: dict) -> bool:
    """A skiftleder who is not in training is working this shift."""
    return any(
        place.kind == "skiftleder"
        and place.assignment is not None
        and not place.assignment.cannot_attend
        and not _is_sl_trainee(trainings, place.assignment.volunteer_id)
        for place in places
    )


def _mark_trainees(places: list[Place], starts_at, trainings: dict) -> None:
    """Training text on each trainee's place, and a yellow note for a skiftleder in training on a
    shift where no skiftleder who is not in training is working."""
    if not trainings:
        return
    experienced = _has_experienced_skiftleder(places, trainings)
    for place in places:
        assignment = place.assignment
        if assignment is None or assignment.cannot_attend or assignment.volunteer_id not in trainings:
            continue
        mine = trainings[assignment.volunteer_id]
        pick = next((v for v in mine if (v.record.kind == "skiftleder") == (place.kind == "skiftleder")), mine[0])
        if starts_at is not None:
            assignment.training = place_text(pick.record, pick.attended, starts_at)
        if _is_sl_trainee(trainings, assignment.volunteer_id) and not experienced:
            assignment.problems.append(Note(text=SL_TRAINEE_ALONE, level="yellow"))


SL_TRAINEE_ALONE = "Skiftleder i opplæring, men ingen erfaren skiftleder er på skiftet"


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
    changes: dict[date, tuple[datetime, str]] | None = None,
    suggestions=None,
    trainings: dict | None = None,
) -> WeekBoard:
    """``changes``: Monday -> (when, by whom) for published weeks changed after publishing.
    ``suggestions``: the pending ``SuggestionRecord``s of the week; each is drawn on the next truly
    empty place of its slot (a suggestion never fills a "kan ikke i dag" place)."""
    pending: dict[int, list] = {}
    for record in suggestions or []:
        pending.setdefault(record.slot_id, []).append(record)
    trainings = trainings or {}
    changes = changes or {}
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
                box = _place(place, seat, closed=cell.is_closed)
                if box.assignment is None and pending.get(box.slot_id):
                    record = pending[box.slot_id].pop(0)
                    box.suggestion = Suggestion(volunteer_id=record.volunteer_id, why=record.why)
                places.append(box)
            start, end = times.get(cell.shift_id, (None, None))
            _mark_trainees(places, start, trainings)
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

    fit = {
        volunteer_id: {slot_id: _fit(raw) for slot_id, raw in per_slot.items()}
        for volunteer_id, per_slot in view.data["place_state"].items()
    }
    # Placing a skiftleder in training where no skiftleder who is not in training works: a warning,
    # never a refusal (the leader may decide otherwise).
    experienced_by_slot = {
        place.slot_id: _has_experienced_skiftleder(shift.places, trainings)
        for shift in shifts
        for place in shift.places
    }
    for volunteer_id, per_slot in fit.items():
        # The picker data is keyed with text ("103"), the board with numbers.
        if not _is_sl_trainee(trainings, int(volunteer_id)):
            continue
        for slot_id, result in per_slot.items():
            if result.ok and not experienced_by_slot.get(int(slot_id), True):
                result.warnings.append(Note(text=SL_TRAINEE_ALONE, level="yellow"))

    board = WeekBoard(
        bar=LeaderBar(id=view.bar_id, name=view.bar_name),
        week_start=view.week_start.isoformat(),
        week_number=view.week_start.isocalendar().week,
        version="",
        status=_status_of(view.status, view.week_start, changes),
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
                tags=[v.progress.text for v in trainings.get(person.volunteer_id, [])],
                training=[
                    PersonTraining(
                        qualification=v.record.qualification,
                        kind=v.record.kind,
                        done=v.progress.done,
                        required=v.progress.required,
                        ready=v.progress.ready,
                        text=v.progress.text,
                        courses_pending=[name for _, name in v.pending_courses],
                    )
                    for v in trainings.get(person.volunteer_id, [])
                ],
            )
            for person in view.people
        ],
        fit=fit,
        strip=[
            WeekStripItem(week_start=item.week_start.isoformat(), number=item.number, status=_status_of(item.status, item.week_start, changes))
            for item in view.strip
        ],
        last_changed=(
            LastChanged(by=changes[view.week_start][1], at=changes[view.week_start][0].astimezone(OSLO).isoformat())
            if view.week_start in changes and view.status == "published"
            else None
        ),
    )
    return board.model_copy(update={"version": _version(board)})


def _version(board: WeekBoard) -> str:
    """A fingerprint of everything shown: it changes when any of it changes."""
    text = json.dumps(board.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]
