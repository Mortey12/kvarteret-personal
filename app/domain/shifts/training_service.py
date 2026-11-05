"""Training on the person, as the leaders use it: see who is in training and how far they have
come, put someone in training, approve them, give them one more shift, or take them out again.
The rules and the counting are in ``training.py``; the data is the qualification rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.service import ShiftsService
from app.shared.semester import get_current_semester_code
from app.domain.shifts.qualification_types import QualificationType, ancestors, offered_in
from app.domain.shifts.training import (
    TrainingProgress,
    TrainingRecord,
    progress,
    training_records,
)

# Approving a finished training also registers the course marked for that qualification
# (``courses.training_qualification``), and no other. A group that does not use this has no marked
# course, and then nothing is registered. Courses for everyone (fire safety and so on) are never touched.


@dataclass(slots=True)
class TrainingView:
    record: TrainingRecord
    progress: TrainingProgress
    attended: list[tuple[datetime, datetime]]
    # The course of this qualification, when it exists and the person has not completed it yet.
    pending_courses: list[tuple[int, str]] = field(default_factory=list)


async def types_for_bar(service: ShiftsService, group_id: int) -> list[QualificationType]:
    """The active qualification types a leader of this bar can train people for."""
    chain = ancestors(group_id, await service.repository.list_group_parents())
    return offered_in(await service.repository.list_qualification_types(), chain)


async def training_views(service: ShiftsService, group_id: int) -> dict[int, list[TrainingView]]:
    """Everyone in training in a bar, by volunteer id, with how far they have come."""
    repository = service.repository
    settings = (await repository.get_qualification_settings([group_id]))[group_id]
    types = {t.key: t for t in await repository.list_qualification_types()}
    records = training_records(await repository.list_group_qualifications([group_id]), settings, group_id, types)
    if not records:
        return {}
    since = min(r.started_at for r in records)
    attended = await repository.list_attended(group_id, sorted({r.volunteer_id for r in records}), since)
    now = service._now()
    marked = await repository.list_training_courses()
    done = await repository.list_completed_courses(
        sorted({r.volunteer_id for r in records}), sorted(course_id for course_id, _ in marked.values())
    )
    views: dict[int, list[TrainingView]] = {}
    for record in records:
        mine = attended.get(record.volunteer_id, [])
        course_id, name = marked.get(record.qualification, (None, ""))
        pending = (
            [(course_id, name)]
            if course_id is not None and course_id not in done.get(record.volunteer_id, set())
            else []
        )
        views.setdefault(record.volunteer_id, []).append(
            TrainingView(record, progress(record, mine, now), mine, pending)
        )
    return views


async def _require(service: ShiftsService, group_id: int, scope: GroupScope) -> None:
    name = await service.repository.get_group_name(group_id)
    if name is None:
        raise ShiftNotFoundError("Gruppen finnes ikke.")
    scope.require(group_id, name)


async def _find(service: ShiftsService, group_id: int, volunteer_id: int, qualification: str) -> TrainingView:
    found = next(
        (v for v in (await training_views(service, group_id)).get(volunteer_id, []) if v.record.qualification == qualification),
        None,
    )
    if found is None:
        raise ShiftPlacementError("Personen er ikke i opplæring her.")
    return found


async def start_training(
    service: ShiftsService, *, group_id: int, volunteer_id: int, qualification: str, scope: GroupScope
) -> str:
    await _require(service, group_id, scope)
    offered = {t.key: t for t in await types_for_bar(service, group_id)}
    if qualification not in offered:
        raise ShiftPlacementError("Denne opplæringen finnes ikke for denne baren.")
    word = offered[qualification].label
    name = await service.repository.get_volunteer_name(volunteer_id)
    if name is None:
        raise ShiftNotFoundError("Personen finnes ikke.")
    existing = [r for r in await service.repository.list_group_qualifications([group_id]) if r.volunteer_id == volunteer_id]
    if any(r.qualification == qualification for r in existing):
        state = "er allerede godkjent" if next(r for r in existing if r.qualification == qualification).status == "approved" else "er allerede i opplæring"
        raise ShiftPlacementError(f"{name} {state} som {word}.")
    await service.repository.start_training(volunteer_id, qualification, group_id)
    return f"{name} er satt i opplæring som {word}."


async def approve_training(
    service: ShiftsService,
    *,
    group_id: int,
    volunteer_id: int,
    qualification: str,
    scope: GroupScope,
    approved_by,
    register_courses: bool = True,
) -> str:
    await _require(service, group_id, scope)
    view = await _find(service, group_id, volunteer_id, qualification)
    name = await service.repository.get_volunteer_name(volunteer_id) or "Personen"
    if not view.progress.ready:
        raise ShiftPlacementError(
            f"{name} har bare {view.progress.done} av {view.progress.required} skift. Vent til opplæringen er ferdig."
        )
    await service.repository.approve_training(volunteer_id, qualification, group_id, approved_by, service._now())
    message = f"{name} er godkjent som {view.record.label or qualification}."
    if register_courses and view.pending_courses:
        semester = get_current_semester_code(service._now())
        for course_id, _ in view.pending_courses:
            await service.repository.add_course_completion(volunteer_id, course_id, semester)
        names = ", ".join(course for _, course in view.pending_courses)
        message += f" Kurs registrert som fullført: {names}."
    return message


async def extend_training(
    service: ShiftsService, *, group_id: int, volunteer_id: int, qualification: str, scope: GroupScope
) -> str:
    await _require(service, group_id, scope)
    view = await _find(service, group_id, volunteer_id, qualification)
    name = await service.repository.get_volunteer_name(volunteer_id) or "Personen"
    if not view.progress.ready:
        raise ShiftPlacementError(f"{name} er ikke ferdig med skiftene sine ennå.")
    await service.repository.set_training_target(volunteer_id, qualification, group_id, view.record.required + 1)
    return f"{name} fikk ett skift til i opplæringen ({view.record.required + 1} i alt)."


async def stop_training(
    service: ShiftsService, *, group_id: int, volunteer_id: int, qualification: str, scope: GroupScope
) -> str:
    await _require(service, group_id, scope)
    await _find(service, group_id, volunteer_id, qualification)
    name = await service.repository.get_volunteer_name(volunteer_id) or "Personen"
    await service.repository.stop_training(volunteer_id, qualification, group_id)
    return f"{name} er tatt ut av opplæringen."
