from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID

from app.auth.roles import UserRole
from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import (
    MemberRole,
    PlacementResult,
    PoolPerson,
    ShiftRecord,
    SlotRecord,
    WeekPlan,
)
from app.domain.shifts.permissions import GroupScope, manageable_groups
from app.domain.shifts.planning import (
    OSLO,
    MISSING_BIRTH_DATE_WARNING,
    VENUE_MIN_AGE,
    already_on_shifts_warning,
    absent_to_replace,
    age_problem,
    allowed_tokens,
    format_day,
    format_week,
    group_into_days,
    monday_of,
    open_for_everyone,
    open_places,
    placement_problem,
    slot_kind,
)
from app.domain.shifts.repository import ShiftsRepository
from app.shared.semester import get_current_semester_code


class ShiftsServiceProtocol(Protocol):
    async def manageable_scope(self, role: UserRole, auth_user_id: UUID) -> GroupScope: ...
    async def get_week_plan(
        self, week: date, group_id: int | None = None, *, scope: GroupScope
    ) -> WeekPlan: ...
    async def assign(self, *, slot_id: int, volunteer_id: int, scope: GroupScope) -> PlacementResult: ...
    async def move(self, *, assignment_id: int, slot_id: int, scope: GroupScope) -> PlacementResult: ...
    async def remove(self, *, assignment_id: int, scope: GroupScope) -> PlacementResult: ...


def _week_bounds(day: date) -> tuple[datetime, datetime]:
    week_start = monday_of(day)
    return (
        datetime.combine(week_start, time.min, tzinfo=OSLO),
        datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=OSLO),
    )


def _roles_by_group(members: list[MemberRole]) -> dict[int, dict[int, set[str]]]:
    """volunteer id -> group id -> verv names."""
    result: dict[int, dict[int, set[str]]] = {}
    for member in members:
        groups = result.setdefault(member.volunteer_id, {})
        roles = groups.setdefault(member.group_id, set())
        if member.role_name:
            roles.add(member.role_name)
    return result


class ShiftsService:
    def __init__(self, repository: ShiftsRepository) -> None:
        self.repository = repository

    async def manageable_scope(self, role: UserRole, auth_user_id: UUID) -> GroupScope:
        if role != UserRole.GROUP_ADMIN:
            return manageable_groups(role, [], {})
        assigned = await self.repository.list_group_admin_group_ids(auth_user_id)
        return manageable_groups(role, assigned, await self.repository.list_group_parents())

    async def get_week_plan(
        self, week: date, group_id: int | None = None, *, scope: GroupScope
    ) -> WeekPlan:
        week_start = monday_of(week)
        starts_from, starts_before = _week_bounds(week_start)
        all_groups = await self.repository.list_shift_groups()
        group_options = [option for option in all_groups if scope.allows(option.group_id)]
        if group_id is not None and all(option.group_id != group_id for option in group_options):
            group_id = None
        records = (
            []
            if not group_options
            else await self.repository.list_shifts_between(
                starts_from,
                starts_before,
                group_id=group_id,
                group_ids=[option.group_id for option in group_options],
            )
        )
        week_counts = await self.repository.count_shifts_by_volunteer(starts_from, starts_before)
        await self._attach_warnings(records, week_counts)
        days = group_into_days(records)
        open_count = sum(
            shift.open_places for day in days for venue in day.venues for shift in venue.shifts
        )
        swap_requests = sum(
            1
            for record in records
            for slot in record.slots
            for person in slot.people
            if person.swap_requested
        )
        pool = await self._pool(
            [option.group_id for option in all_groups],
            [option.group_id for option in group_options],
            group_id,
            week_counts,
        )
        return WeekPlan(
            week_start=week_start,
            week_label=format_week(week_start),
            previous_week=week_start - timedelta(days=7),
            next_week=week_start + timedelta(days=7),
            selected_group_id=group_id,
            groups=group_options,
            days=days,
            open_places=open_count,
            swap_requests=swap_requests,
            pool=pool,
        )

    async def _pool(
        self,
        shift_group_ids: list[int],
        visible_group_ids: list[int],
        selected_group_id: int | None,
        week_counts: dict[int, int],
    ) -> list[PoolPerson]:
        members = await self.repository.list_shift_members(
            get_current_semester_code(), shift_group_ids
        )
        roles = _roles_by_group(members)
        pool: list[PoolPerson] = []
        seen: set[int] = set()
        for member in members:
            if member.group_id not in visible_group_ids:
                continue
            if selected_group_id is not None and member.group_id != selected_group_id:
                continue
            if member.volunteer_id in seen:
                continue
            seen.add(member.volunteer_id)
            pool.append(
                PoolPerson(
                    volunteer_id=member.volunteer_id,
                    name=member.name,
                    group_name=member.group_name,
                    role_name=member.role_name or "Medlem",
                    shifts_this_week=week_counts.get(member.volunteer_id, 0),
                    allowed_tokens=allowed_tokens(roles[member.volunteer_id], visible_group_ids),
                )
            )
        return pool

    async def _attach_warnings(
        self, records: list[ShiftRecord], week_counts: dict[int, int]
    ) -> None:
        """The same hints ``assign`` gives, on each placed person, so a reload keeps them."""
        young_ids = sorted(
            {
                person.volunteer_id
                for record in records
                if record.venue in VENUE_MIN_AGE
                for slot in record.slots
                for person in slot.people
            }
        )
        birth_dates = await self.repository.get_birth_dates(young_ids)
        for record in records:
            for slot in record.slots:
                for person in slot.people:
                    others = week_counts.get(person.volunteer_id, 0) - 1
                    already = already_on_shifts_warning(others)
                    if already:
                        person.warnings.append(already)
                    if (
                        record.venue in VENUE_MIN_AGE
                        and birth_dates.get(person.volunteer_id) is None
                    ):
                        person.warnings.append(MISSING_BIRTH_DATE_WARNING)

    async def _load_slot(self, slot_id: int, scope: GroupScope) -> tuple[ShiftRecord, SlotRecord]:
        shift_id = await self.repository.get_slot_shift_id(slot_id)
        shift = await self.repository.get_shift(shift_id) if shift_id is not None else None
        slot = next((item for item in shift.slots if item.slot_id == slot_id), None) if shift else None
        if shift is None or slot is None:
            raise ShiftNotFoundError("Plassen finnes ikke lenger. Oppdater siden.")
        scope.require(shift.group_id, shift.group_name)
        return shift, slot

    async def _check(
        self,
        shift: ShiftRecord,
        slot: SlotRecord,
        volunteer_id: int,
        *,
        moving_assignment_id: int | None = None,
    ) -> list[str]:
        """Raise when the placement breaks a rule; return warnings otherwise."""
        if shift.is_closed:
            raise ShiftPlacementError("Skiftet er stengt.")
        on_shift = [
            person
            for item in shift.slots
            for person in item.people
            if person.volunteer_id == volunteer_id and person.assignment_id != moving_assignment_id
        ]
        if on_shift:
            raise ShiftPlacementError("Personen står allerede på dette skiftet.")
        # Room is counted as if the moved person had already left their old place.
        if moving_assignment_id is not None:
            for item in shift.slots:
                item.people = [p for p in item.people if p.assignment_id != moving_assignment_id]
        groups = await self.repository.list_shift_groups()
        members = await self.repository.list_shift_members(
            get_current_semester_code(), [group.group_id for group in groups]
        )
        roles = _roles_by_group(members).get(volunteer_id, {})
        problem = placement_problem(slot.kind, shift.group_id, open_for_everyone(slot), roles)
        if problem:
            raise ShiftPlacementError(problem + ".")
        warnings: list[str] = []
        if shift.venue in VENUE_MIN_AGE:
            birth_date = await self.repository.get_volunteer_birth_date(volunteer_id)
            too_young = age_problem(shift.venue, birth_date, shift.starts_at.astimezone(OSLO).date())
            if too_young:
                raise ShiftPlacementError(too_young + ".")
            if birth_date is None:
                warnings.append(MISSING_BIRTH_DATE_WARNING)
        if open_places(shift, slot) <= 0:
            if slot.kind == "opplaering" and len(slot.people) < slot.count:
                raise ShiftPlacementError(
                    "Ingen ledig plass til opplæring: de vanlige plassene er fylt."
                )
            raise ShiftPlacementError("Plassen er full.")
        starts_from, starts_before = _week_bounds(shift.starts_at.astimezone(OSLO).date())
        counts = await self.repository.count_shifts_by_volunteer(starts_from, starts_before)
        already = counts.get(volunteer_id, 0)
        if moving_assignment_id is not None:
            moving = await self.repository.get_assignment(moving_assignment_id)
            old_shift = await self.repository.get_shift(moving.shift_id) if moving else None
            if old_shift and starts_from <= old_shift.starts_at < starts_before:
                already -= 1
        if message := already_on_shifts_warning(already):
            warnings.append(message)
        return warnings

    @staticmethod
    def _describe(shift: ShiftRecord, slot: SlotRecord) -> str:
        day = format_day(shift.starts_at.astimezone(OSLO).date()).lower()
        return f"{slot_kind(slot.kind).label.lower()}, {shift.title.lower()} {day}"

    async def assign(
        self, *, slot_id: int, volunteer_id: int, scope: GroupScope
    ) -> PlacementResult:
        shift, slot = await self._load_slot(slot_id, scope)
        name = await self.repository.get_volunteer_name(volunteer_id)
        if name is None:
            raise ShiftNotFoundError("Personen finnes ikke.")
        warnings = await self._check(shift, slot, volunteer_id)
        await self._take_place(shift, slot)
        await self.repository.add_assignment(slot_id, volunteer_id)
        return PlacementResult(_message(f"{name} er satt opp som {self._describe(shift, slot)}.", warnings))

    async def _take_place(self, shift: ShiftRecord, slot: SlotRecord) -> None:
        """Someone who said "kan ikke i dag" comes off when their place is taken."""
        absent = absent_to_replace(shift, slot)
        if absent is not None:
            await self.repository.remove_assignment(absent.assignment_id)

    async def move(
        self, *, assignment_id: int, slot_id: int, scope: GroupScope
    ) -> PlacementResult:
        assignment = await self.repository.get_assignment(assignment_id)
        if assignment is None:
            raise ShiftNotFoundError("Tildelingen finnes ikke lenger. Oppdater siden.")
        await self._load_slot(assignment.slot_id, scope)
        if assignment.slot_id == slot_id:
            return PlacementResult("Ingen endring.")
        shift, slot = await self._load_slot(slot_id, scope)
        warnings = await self._check(
            shift, slot, assignment.volunteer_id, moving_assignment_id=assignment_id
        )
        await self._take_place(shift, slot)
        await self.repository.move_assignment(assignment_id, slot_id)
        name = await self.repository.get_volunteer_name(assignment.volunteer_id) or "Personen"
        return PlacementResult(_message(f"{name} er flyttet til {self._describe(shift, slot)}.", warnings))

    async def remove(self, *, assignment_id: int, scope: GroupScope) -> PlacementResult:
        assignment = await self.repository.get_assignment(assignment_id)
        if assignment is None:
            raise ShiftNotFoundError("Tildelingen finnes ikke lenger. Oppdater siden.")
        shift, slot = await self._load_slot(assignment.slot_id, scope)
        await self.repository.remove_assignment(assignment_id)
        name = await self.repository.get_volunteer_name(assignment.volunteer_id) or "Personen"
        return PlacementResult(f"{name} er tatt av {self._describe(shift, slot)}. Plassen er ledig igjen.")


def _message(text: str, warnings: list[str]) -> str:
    if not warnings:
        return text
    return f"{text} Merk: {', '.join(warnings)}."
