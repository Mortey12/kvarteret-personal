from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import (
    AssignedPerson,
    AssignmentRef,
    MemberRole,
    ShiftGroupOption,
    ShiftRecord,
    SlotRecord,
)
from app.domain.shifts.service import ShiftsService


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
        return next((m.name for m in self.members if m.volunteer_id == volunteer_id), None)

    async def add_assignment(self, slot_id: int, volunteer_id: int) -> int:
        self.added.append((slot_id, volunteer_id))
        return 1

    async def get_assignment(self, assignment_id: int) -> AssignmentRef | None:
        return self.assignments.get(assignment_id)

    async def move_assignment(self, assignment_id: int, slot_id: int) -> None:
        self.moved.append((assignment_id, slot_id))

    async def remove_assignment(self, assignment_id: int) -> None:
        self.removed.append(assignment_id)

    def place(self, slot_id: int, volunteer_id: int, assignment_id: int) -> None:
        slot = next(s for s in self.shift.slots if s.slot_id == slot_id)
        slot.people.append(AssignedPerson(assignment_id, volunteer_id, "x", False))
        self.assignments[assignment_id] = AssignmentRef(assignment_id, slot_id, 1, volunteer_id)


@pytest.mark.asyncio
async def test_assign_skiftleder() -> None:
    repo = FakeRepository()
    result = await ShiftsService(repo).assign(slot_id=10, volunteer_id=100)
    assert repo.added == [(10, 100)]
    assert result.message.startswith("Sigrid Skiftleder er satt opp som skiftleder, dagskift mandag 2. november")


@pytest.mark.asyncio
async def test_skjenker_cannot_take_skiftleder_place() -> None:
    repo = FakeRepository()
    with pytest.raises(ShiftPlacementError, match="Kun for skiftledere"):
        await ShiftsService(repo).assign(slot_id=10, volunteer_id=101)
    assert repo.added == []


@pytest.mark.asyncio
async def test_trainee_takes_the_crew_place() -> None:
    repo = FakeRepository()
    repo.place(12, 102, assignment_id=5)
    with pytest.raises(ShiftPlacementError, match="Plassen er full"):
        await ShiftsService(repo).assign(slot_id=11, volunteer_id=101)


@pytest.mark.asyncio
async def test_no_room_for_trainee_when_crew_is_full() -> None:
    repo = FakeRepository()
    repo.place(11, 101, assignment_id=5)
    with pytest.raises(ShiftPlacementError, match="Ingen ledig plass til opplæring"):
        await ShiftsService(repo).assign(slot_id=12, volunteer_id=102)


@pytest.mark.asyncio
async def test_same_person_twice_on_a_shift_is_refused() -> None:
    repo = FakeRepository()
    repo.place(10, 100, assignment_id=5)
    with pytest.raises(ShiftPlacementError, match="allerede på dette skiftet"):
        await ShiftsService(repo).assign(slot_id=11, volunteer_id=100)


@pytest.mark.asyncio
async def test_closed_shift_is_refused() -> None:
    repo = FakeRepository()
    repo.shift.is_closed = True
    with pytest.raises(ShiftPlacementError, match="stengt"):
        await ShiftsService(repo).assign(slot_id=10, volunteer_id=100)


@pytest.mark.asyncio
async def test_second_shift_in_a_week_is_allowed_with_a_warning() -> None:
    repo = FakeRepository()
    repo.week_counts = {101: 1}
    result = await ShiftsService(repo).assign(slot_id=11, volunteer_id=101)
    assert "Merk: har allerede 1 skift denne uka" in result.message


@pytest.mark.asyncio
async def test_move_within_a_shift_frees_the_old_place() -> None:
    repo = FakeRepository()
    repo.place(10, 100, assignment_id=5)
    repo.week_counts = {100: 1}
    result = await ShiftsService(repo).move(assignment_id=5, slot_id=11)
    assert repo.moved == [(5, 11)]
    assert "Merk" not in result.message


@pytest.mark.asyncio
async def test_remove_and_missing_assignment() -> None:
    repo = FakeRepository()
    repo.place(11, 101, assignment_id=5)
    result = await ShiftsService(repo).remove(assignment_id=5)
    assert repo.removed == [5]
    assert "Plassen er ledig igjen" in result.message
    with pytest.raises(ShiftNotFoundError):
        await ShiftsService(repo).remove(assignment_id=99)
