from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from uuid import uuid4

from app.auth.roles import UserRole
from app.domain.shifts.errors import ShiftForbiddenError, ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import (
    AssignedPerson,
    AssignmentRef,
    MemberRole,
    ShiftGroupOption,
    ShiftRecord,
    SlotRecord,
)
from app.domain.shifts.permissions import ALL_GROUPS, NO_GROUPS, GroupScope
from app.domain.shifts.service import ShiftsService

SCOPE = ALL_GROUPS


class FakeRepository:
    def __init__(self) -> None:
        start = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)
        self.shift = ShiftRecord(
            shift_id=1,
            group_id=7,
            group_name="Stjerne",
            venue="Stjernesalen",
            title="Dagskift",
            starts_at=start,
            ends_at=start.replace(hour=16),
            note=None,
            is_closed=False,
            training_replaces_slot=True,
            slots=[
                SlotRecord(10, "skiftleder", 1, False, 0, []),
                SlotRecord(11, "skjenker", 1, True, 1, []),
                SlotRecord(12, "opplaering", 1, False, 2, []),
            ],
        )
        self.members = [
            MemberRole(100, "Sigrid Skiftleder", 7, "Stjerne", "Skiftleder"),
            MemberRole(101, "Sander Skjenker", 7, "Stjerne", "Skjenker"),
            MemberRole(102, "Tora Trainee", 7, "Stjerne", "Opplæring"),
        ]
        self.week_counts: dict[int, int] = {}
        self.added: list[tuple[int, int]] = []
        self.moved: list[tuple[int, int]] = []
        self.removed: list[int] = []
        self.assignments: dict[int, AssignmentRef] = {}
        self.birth_dates: dict[int, date | None] = {}
        # People outside Skjenke (samfunnettjeneste).
        self.outsiders: dict[int, str] = {}
        self.admin_group_ids: list[int] = []
        self.parents: dict[int, int | None] = {7: 1, 8: 1, 1: None}

    async def get_slot_shift_id(self, slot_id: int) -> int | None:
        return 1 if slot_id in (10, 11, 12) else None

    async def get_shift(self, shift_id: int) -> ShiftRecord | None:
        # A fresh copy each time, like a database read.
        return ShiftRecord(
            **{
                **{f: getattr(self.shift, f) for f in self.shift.__dataclass_fields__},
                "slots": [
                    SlotRecord(s.slot_id, s.kind, s.count, s.open_for_samf, s.sort_order, list(s.people))
                    for s in self.shift.slots
                ],
            }
        )

    async def list_shift_groups(self) -> list[ShiftGroupOption]:
        return [ShiftGroupOption(7, "Stjerne")]

    async def list_shift_members(self, semester: int, group_ids: list[int]) -> list[MemberRole]:
        return self.members

    async def count_shifts_by_volunteer(self, starts_from, starts_before) -> dict[int, int]:
        return self.week_counts

    async def get_volunteer_name(self, volunteer_id: int) -> str | None:
        if volunteer_id in self.outsiders:
            return self.outsiders[volunteer_id]
        return next((m.name for m in self.members if m.volunteer_id == volunteer_id), None)

    async def get_volunteer_birth_date(self, volunteer_id: int) -> date | None:
        return self.birth_dates.get(volunteer_id)

    async def get_birth_dates(self, volunteer_ids: list[int]) -> dict[int, date | None]:
        return {vid: self.birth_dates.get(vid) for vid in volunteer_ids}

    async def list_shifts_between(
        self, starts_from, starts_before, group_id=None, *, group_ids=None
    ) -> list[ShiftRecord]:
        shift = await self.get_shift(1)
        if group_ids is not None and shift.group_id not in group_ids:
            return []
        return [shift]

    async def list_group_admin_group_ids(self, auth_user_id) -> list[int]:
        return self.admin_group_ids

    async def list_group_parents(self) -> dict[int, int | None]:
        return self.parents

    async def add_assignment(self, slot_id: int, volunteer_id: int) -> int:
        self.added.append((slot_id, volunteer_id))
        return 1

    async def get_assignment(self, assignment_id: int) -> AssignmentRef | None:
        return self.assignments.get(assignment_id)

    async def move_assignment(self, assignment_id: int, slot_id: int) -> None:
        self.moved.append((assignment_id, slot_id))

    async def remove_assignment(self, assignment_id: int) -> None:
        self.removed.append(assignment_id)

    def place(self, slot_id: int, volunteer_id: int, assignment_id: int, *, absent: bool = False) -> None:
        slot = next(s for s in self.shift.slots if s.slot_id == slot_id)
        slot.people.append(AssignedPerson(assignment_id, volunteer_id, "x", False, cannot_attend=absent))
        self.assignments[assignment_id] = AssignmentRef(assignment_id, slot_id, 1, volunteer_id)


@pytest.mark.asyncio
async def test_assign_skiftleder() -> None:
    repo = FakeRepository()
    result = await ShiftsService(repo).assign(slot_id=10, volunteer_id=100, scope=SCOPE)
    assert repo.added == [(10, 100)]
    assert result.message.startswith("Sigrid Skiftleder er satt opp som skiftleder, dagskift mandag 2. november")


@pytest.mark.asyncio
async def test_skjenker_cannot_take_skiftleder_place() -> None:
    repo = FakeRepository()
    with pytest.raises(ShiftPlacementError, match="Kun for skiftledere"):
        await ShiftsService(repo).assign(slot_id=10, volunteer_id=101, scope=SCOPE)
    assert repo.added == []


@pytest.mark.asyncio
async def test_trainee_takes_the_crew_place() -> None:
    repo = FakeRepository()
    repo.place(12, 102, assignment_id=5)
    with pytest.raises(ShiftPlacementError, match="Plassen er full"):
        await ShiftsService(repo).assign(slot_id=11, volunteer_id=101, scope=SCOPE)


@pytest.mark.asyncio
async def test_no_room_for_trainee_when_crew_is_full() -> None:
    repo = FakeRepository()
    repo.place(11, 101, assignment_id=5)
    with pytest.raises(ShiftPlacementError, match="Ingen ledig plass til opplæring"):
        await ShiftsService(repo).assign(slot_id=12, volunteer_id=102, scope=SCOPE)


@pytest.mark.asyncio
async def test_same_person_twice_on_a_shift_is_refused() -> None:
    repo = FakeRepository()
    repo.place(10, 100, assignment_id=5)
    with pytest.raises(ShiftPlacementError, match="allerede på dette skiftet"):
        await ShiftsService(repo).assign(slot_id=11, volunteer_id=100, scope=SCOPE)


@pytest.mark.asyncio
async def test_closed_shift_is_refused() -> None:
    repo = FakeRepository()
    repo.shift.is_closed = True
    with pytest.raises(ShiftPlacementError, match="stengt"):
        await ShiftsService(repo).assign(slot_id=10, volunteer_id=100, scope=SCOPE)


@pytest.mark.asyncio
async def test_second_shift_in_a_week_is_allowed_with_a_warning() -> None:
    repo = FakeRepository()
    repo.week_counts = {101: 1}
    result = await ShiftsService(repo).assign(slot_id=11, volunteer_id=101, scope=SCOPE)
    assert "Merk: har allerede 1 skift denne uka" in result.message


@pytest.mark.asyncio
async def test_move_within_a_shift_frees_the_old_place() -> None:
    repo = FakeRepository()
    repo.place(10, 100, assignment_id=5)
    repo.week_counts = {100: 1}
    result = await ShiftsService(repo).move(assignment_id=5, slot_id=11, scope=SCOPE)
    assert repo.moved == [(5, 11)]
    assert "Merk" not in result.message


@pytest.mark.asyncio
async def test_remove_and_missing_assignment() -> None:
    repo = FakeRepository()
    repo.place(11, 101, assignment_id=5)
    result = await ShiftsService(repo).remove(assignment_id=5, scope=SCOPE)
    assert repo.removed == [5]
    assert "Plassen er ledig igjen" in result.message
    with pytest.raises(ShiftNotFoundError):
        await ShiftsService(repo).remove(assignment_id=99, scope=SCOPE)


@pytest.mark.asyncio
async def test_taking_a_place_left_by_kan_ikke_takes_the_absent_person_off() -> None:
    repo = FakeRepository()
    repo.place(11, 101, assignment_id=5, absent=True)
    repo.members.append(MemberRole(103, "Siri Skjenker", 7, "Stjerne", "Skjenker"))
    await ShiftsService(repo).assign(slot_id=11, volunteer_id=103, scope=SCOPE)
    assert repo.removed == [5]
    assert repo.added == [(11, 103)]


@pytest.mark.asyncio
async def test_place_left_by_kan_ikke_opens_for_samfunnettjeneste() -> None:
    repo = FakeRepository()
    repo.shift.slots[1].open_for_samf = False
    repo.outsiders[555] = "Ola Samf"
    with pytest.raises(ShiftPlacementError, match="Kun for Skjenke"):
        await ShiftsService(repo).assign(slot_id=11, volunteer_id=555, scope=SCOPE)
    repo.place(11, 101, assignment_id=5, absent=True)
    await ShiftsService(repo).assign(slot_id=11, volunteer_id=555, scope=SCOPE)
    assert repo.added == [(11, 555)]


@pytest.mark.asyncio
async def test_halvtimen_needs_20() -> None:
    repo = FakeRepository()
    repo.shift.venue = "Halvtimen"
    repo.birth_dates[101] = date(2007, 1, 1)
    with pytest.raises(ShiftPlacementError, match="Halvtimen krever 20 år"):
        await ShiftsService(repo).assign(slot_id=11, volunteer_id=101, scope=SCOPE)
    result = await ShiftsService(repo).assign(slot_id=11, volunteer_id=100, scope=SCOPE)
    assert "fødselsdato mangler" in result.message


@pytest.mark.asyncio
async def test_board_keeps_warnings_on_placed_people_after_a_reload() -> None:
    repo = FakeRepository()
    repo.place(11, 101, 1)
    repo.week_counts = {101: 2, 100: 1}
    repo.shift.venue = "Halvtimen"
    repo.place(10, 100, 2)

    plan = await ShiftsService(repo).get_week_plan(date(2026, 11, 2), scope=SCOPE)

    people = {
        person.volunteer_id: person
        for day in plan.days
        for venue in day.venues
        for shift in venue.shifts
        for slot in shift.slots
        for person in slot.people
    }
    # The same texts assign gives; 101 has one other shift this week, no birth date on file.
    assert people[101].warnings == ["har allerede 1 skift denne uka", "fødselsdato mangler, sjekk alderen"]
    assert people[100].warnings == ["fødselsdato mangler, sjekk alderen"]


@pytest.mark.asyncio
async def test_board_has_no_warnings_for_a_single_shift_at_a_plain_venue() -> None:
    repo = FakeRepository()
    repo.place(11, 101, 1)
    repo.week_counts = {101: 1}

    plan = await ShiftsService(repo).get_week_plan(date(2026, 11, 2), scope=SCOPE)

    [person] = [
        person
        for day in plan.days
        for venue in day.venues
        for shift in venue.shifts
        for slot in shift.slots
        for person in slot.people
    ]
    assert person.warnings == []


STJERNE_ONLY = GroupScope(frozenset({7}))


@pytest.mark.asyncio
async def test_plan_only_includes_permitted_groups() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo)
    allowed = await service.get_week_plan(date(2026, 11, 2), scope=STJERNE_ONLY)
    assert [g.group_id for g in allowed.groups] == [7]
    assert allowed.days and [p.name for p in allowed.pool]
    other = await service.get_week_plan(date(2026, 11, 2), scope=GroupScope(frozenset({8})))
    assert other.groups == [] and other.days == [] and other.pool == []
    nothing = await service.get_week_plan(date(2026, 11, 2), scope=NO_GROUPS)
    assert nothing.groups == [] and nothing.days == [] and nothing.pool == []


@pytest.mark.asyncio
async def test_forbidden_group_filter_falls_back_to_permitted_groups() -> None:
    plan = await ShiftsService(FakeRepository()).get_week_plan(
        date(2026, 11, 2), 99, scope=STJERNE_ONLY
    )
    assert plan.selected_group_id is None


@pytest.mark.asyncio
async def test_actions_on_a_forbidden_group_are_refused() -> None:
    repo = FakeRepository()
    repo.place(11, 101, assignment_id=5)
    service = ShiftsService(repo)
    elsewhere = GroupScope(frozenset({8}))
    with pytest.raises(ShiftForbiddenError, match="Du har ikke tilgang.*Stjerne"):
        await service.assign(slot_id=10, volunteer_id=100, scope=elsewhere)
    with pytest.raises(ShiftForbiddenError):
        await service.remove(assignment_id=5, scope=elsewhere)
    with pytest.raises(ShiftForbiddenError):
        await service.move(assignment_id=5, slot_id=11, scope=elsewhere)
    assert repo.added == [] and repo.removed == [] and repo.moved == []


@pytest.mark.asyncio
async def test_move_between_permitted_and_forbidden_group_is_refused() -> None:
    repo = FakeRepository()
    repo.place(11, 101, assignment_id=5)
    service = ShiftsService(repo)
    # The target slot (shift 1, Stjerne) is forbidden...
    with pytest.raises(ShiftForbiddenError):
        await service.move(assignment_id=5, slot_id=10, scope=GroupScope(frozenset({8})))
    # ...and so is moving out of a forbidden shift into a permitted one.
    repo.shift.group_id = 8
    repo.shift.group_name = "Grøndahls"
    with pytest.raises(ShiftForbiddenError, match="Grøndahls"):
        await service.move(assignment_id=5, slot_id=12, scope=STJERNE_ONLY)
    assert repo.moved == []


@pytest.mark.asyncio
async def test_scope_for_each_role() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo)
    assert (await service.manageable_scope(UserRole.ADMIN, uuid4())).group_ids is None
    assert (await service.manageable_scope(UserRole.VOLUNTEER, uuid4())).is_empty
    assert (await service.manageable_scope(UserRole.GROUP_ADMIN, uuid4())).is_empty
    repo.admin_group_ids = [1]
    scope = await service.manageable_scope(UserRole.GROUP_ADMIN, uuid4())
    assert scope.group_ids == frozenset({1, 7, 8})
