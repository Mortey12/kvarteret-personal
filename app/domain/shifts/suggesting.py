"""Server side of "Lag forslag for uka": gather the week, ask the real rules who may take which
place, run the engine (``suggest.py``) and save what it proposes as pending suggestions.

The rules stay in ``ShiftsService._check`` (closed shift, already on the shift, qualification, the
venue's age limit); the engine only receives the answers as a set of allowed (place, person) pairs.
Nothing is placed here: a leader accepts or discards the result (``accept_suggestions``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.planning import monday_of, open_places
from app.domain.shifts.service import ShiftsService, _week_bounds
from app.domain.shifts.suggest import PersonSpec, PlaceSpec, SuggestInput, suggest
from app.shared.semester import (
    get_current_semester_code,
    get_previous_semester_code,
    get_semester_end_date,
)

OFFER_WEEKS = (
    4  # "vil" and "kan" answers in this many weeks before count as having offered
)


@dataclass(slots=True)
class SuggestResult:
    message: str
    saved: int
    notes: list[str] = field(default_factory=list)


async def generate_suggestions(
    service: ShiftsService, *, group_id: int, week: date, scope: GroupScope
) -> SuggestResult:
    """Replace the pending suggestions of a bar's week with a fresh set. Never touches placements."""
    repository = service.repository
    name = await repository.get_group_name(group_id)
    if name is None:
        raise ShiftNotFoundError("Gruppen finnes ikke.")
    scope.require(group_id, name)
    monday = monday_of(week)
    start, end = _week_bounds(monday)

    shifts = await repository.list_shifts_between(start, end, group_id)
    await service._lock(*[shift.shift_id for shift in shifts])
    shifts = await repository.list_shifts_between(
        start, end, group_id
    )  # again, under the lock

    semester = get_current_semester_code(service._now())
    members = await repository.list_shift_members(
        semester, await service._pool_group_ids(scope)
    )
    member_names = {member.volunteer_id: member.name for member in members}
    member_ids = sorted(member_names)

    # The empty places that still have to be filled (a "kan ikke i dag" place is not empty here).
    places: list[PlaceSpec] = []
    slot_of: dict[int, tuple] = {}
    for shift in shifts:
        if shift.is_closed:
            continue
        for slot in shift.slots:
            if slot.kind == "opplaering":
                continue
            for seat in range(max(0, open_places(shift, slot, count_absent=True))):
                places.append(
                    PlaceSpec(
                        slot.slot_id,
                        seat,
                        shift.shift_id,
                        slot.kind,
                        shift.starts_at,
                        shift.ends_at,
                    )
                )
            slot_of[slot.slot_id] = (shift, slot)
    if not places:
        return SuggestResult(
            "Ingen endring. Det er ingen tomme plasser å foreslå for.", 0
        )

    answers = await repository.list_wishes_for(
        [shift.shift_id for shift in shifts], member_ids
    )

    # What the rules allow: only asked for people who offered, so few calls reach the rules.
    allowed: set[tuple[int, int]] = set()
    contexts: dict[int, object] = {}
    for place in places:
        shift, slot = slot_of[place.slot_id]
        for volunteer_id in member_ids:
            if answers.get((shift.shift_id, volunteer_id)) not in ("vil", "kan"):
                continue
            if (place.slot_id, volunteer_id) in allowed:
                continue
            if volunteer_id not in contexts:
                contexts[volunteer_id] = await service.eligibility_for(volunteer_id)
            try:
                await service._check(
                    shift, slot, volunteer_id, eligibility=contexts[volunteer_id]
                )
            except ShiftPlacementError:
                continue
            allowed.add((place.slot_id, volunteer_id))

    # What the week already holds, in every group: who is busy when.
    busy: dict[int, list] = {}
    week_shifts: dict[int, int] = {}
    for shift in await repository.list_shifts_between(start, end):
        on_shift = {
            p.volunteer_id
            for slot in shift.slots
            for p in slot.people
            if not p.cannot_attend
        }
        for volunteer_id in on_shift:
            busy.setdefault(volunteer_id, []).append((shift.starts_at, shift.ends_at))
            week_shifts[volunteer_id] = week_shifts.get(volunteer_id, 0) + 1

    # Fairness: shifts so far this semester, and "vil" or "kan" answers in the last weeks.
    semester_start = get_semester_end_date(
        get_previous_semester_code(semester)
    ) + timedelta(days=1)
    history = await repository.count_shifts_by_volunteer(
        _week_bounds(semester_start)[0], start
    )
    recent = await repository.list_shifts_between(
        start - timedelta(weeks=OFFER_WEEKS), start, group_id
    )
    recent_answers = await repository.list_wishes_for(
        [shift.shift_id for shift in recent], member_ids
    )
    offers: dict[int, int] = {}
    for (_, volunteer_id), answer in recent_answers.items():
        if answer in ("vil", "kan"):
            offers[volunteer_id] = offers.get(volunteer_id, 0) + 1

    people = tuple(
        PersonSpec(
            volunteer_id=volunteer_id,
            name=member_names[volunteer_id],
            first_name=member_names[volunteer_id].split(" ")[0],
            week_shifts=week_shifts.get(volunteer_id, 0),
            busy=tuple(busy.get(volunteer_id, ())),
            history=history.get(volunteer_id, 0),
            offers=offers.get(volunteer_id, 0),
        )
        for volunteer_id in member_ids
    )
    output = suggest(
        SuggestInput(
            places=tuple(places),
            people=people,
            answers={
                key: value
                for key, value in answers.items()
                if value in ("vil", "kan", "nei")
            },
            allowed=frozenset(allowed),
        )
    )

    saved = await service.save_suggestions(
        group_id=group_id,
        week=monday,
        items=[(pick.slot_id, pick.volunteer_id, pick.why) for pick in output.picks],
        scope=scope,
    )

    notes: list[str] = []
    if output.unfilled:
        notes.append(
            f"{len(output.unfilled)} plasser fikk ikke noe forslag fordi ingen kunne eller ville."
        )
    if output.without_shift:
        names = ", ".join(member_names[v] for v in output.without_shift[:8])
        more = (
            f" og {len(output.without_shift) - 8} til"
            if len(output.without_shift) > 8
            else ""
        )
        notes.append(f"Ba om skift, men fikk ikke noe forslag: {names}{more}.")
    message = (
        f"Laget {saved} forslag." if saved else "Ingen endring. Fant ingen forslag."
    )
    return SuggestResult(message, saved, notes)
