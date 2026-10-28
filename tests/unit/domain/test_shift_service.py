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
        self.parents: dict[int, int | None] = {7: 1, 8: 1, 1: None, 20: None}
        self.week_rows: dict[tuple[int, date], str] = {}
        self.events: list[tuple] = []
        self.settings: dict[int, dict] = {}

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

    async def list_direct_participating_group_ids(self) -> list[int]:
        return [7, 8]

    async def get_group_name(self, group_id: int) -> str | None:
        return {1: "Skjenkegruppen", 7: "Stjerne", 8: "Grøndahls", 20: "Kultur"}.get(group_id)

    async def get_week_statuses(self, group_ids, weeks) -> dict:
        return {k: v for k, v in self.week_rows.items() if k[0] in group_ids and k[1] in weeks}

    async def set_week_statuses(self, group_id: int, weeks, status: str) -> None:
        for week in weeks:
            self.week_rows[(group_id, week)] = status

    async def list_shift_group_weeks(self, starts_from, starts_before, group_ids) -> set:
        return {(7, date(2026, 11, 2))} if 7 in group_ids else set()

    async def list_groups_with_settings(self) -> list[dict]:
        blank = {"app_enabled": None, "samf_enabled": None, "meeting_point": None, "courses_enabled": None}
        return [
            {"id": gid, "name": name, "parent_group_id": parent, **{**blank, **self.settings.get(gid, {})}}
            for gid, name, parent in (
                (1, "Skjenkegruppen", None),
                (7, "Stjerne", 1),
                (8, "Grøndahls", 1),
                (20, "Kultur", None),
            )
        ]

    async def save_group_settings(
        self, group_id, *, app_enabled, samf_enabled, meeting_point, courses_enabled
    ) -> None:
        self.settings[group_id] = {
            "app_enabled": app_enabled,
            "samf_enabled": samf_enabled,
            "meeting_point": meeting_point,
            "courses_enabled": courses_enabled,
        }

    async def add_assignment(self, slot_id: int, volunteer_id: int) -> int:
        self.added.append((slot_id, volunteer_id))
        return 1

    async def get_assignment(self, assignment_id: int) -> AssignmentRef | None:
        return self.assignments.get(assignment_id)

    async def move_assignment(self, assignment_id: int, slot_id: int) -> None:
        self.moved.append((assignment_id, slot_id))

    async def remove_assignment(self, assignment_id: int) -> None:
        self.removed.append(assignment_id)

    async def lock_shift(self, shift_id: int) -> None:
        self.events.append(("lock", shift_id))

    async def set_shift_closed(self, shift_id: int, closed: bool) -> None:
        self.events.append(("closed", shift_id, closed))
        self.shift.is_closed = closed

    async def delete_shift(self, shift_id: int) -> None:
        self.events.append(("delete", shift_id))

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
    # Shifts stay limited to permitted groups, but the pool has all Skjenke members.
    other = await service.get_week_plan(date(2026, 11, 2), scope=GroupScope(frozenset({8})))
    assert other.groups == [] and other.days == [] and len(other.pool) == 3
    assert all(person.allowed_tokens == [] for person in other.pool)
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
    repo.admin_group_ids = [8]
    assert (await service.manageable_scope(UserRole.GROUP_ADMIN, uuid4())).group_ids == frozenset({8})


@pytest.mark.asyncio
async def test_gruppeadmin_of_an_unrelated_group_sees_nothing() -> None:
    repo = FakeRepository()
    repo.admin_group_ids = [20]
    service = ShiftsService(repo)
    scope = await service.manageable_scope(UserRole.GROUP_ADMIN, uuid4())
    assert scope.is_empty
    plan = await service.get_week_plan(date(2026, 11, 2), scope=scope)
    assert plan.groups == [] and plan.days == [] and plan.pool == []
    assert plan.statuses == [] and plan.banners == []
    assert await service.list_group_settings(scope) == []


@pytest.mark.asyncio
async def test_stjerne_leader_sees_members_of_every_subgroup_in_the_pool() -> None:
    repo = FakeRepository()
    repo.members.append(MemberRole(110, "Gro Grøndahls", 8, "Grøndahls", "Skjenker"))
    plan = await ShiftsService(repo).get_week_plan(date(2026, 11, 2), scope=STJERNE_ONLY)
    by_name = {person.name: person for person in plan.pool}
    assert "Gro Grøndahls" in by_name
    # Tokens only cover the groups the leader may change.
    assert all(token.startswith("7:") for token in by_name["Gro Grøndahls"].allowed_tokens)
    assert by_name["Gro Grøndahls"].allowed_tokens


def _clock(day: date):
    return lambda: datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_week_status_defaults_and_explicit_rows() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo, now=_clock(date(2026, 10, 28)))
    plan = await service.get_week_plan(date(2026, 11, 2), scope=SCOPE)
    assert [(s.group_name, s.status, s.explicit) for s in plan.statuses] == [("Stjerne", "open", False)]
    message = await service.set_week_status(
        group_id=7, week=date(2026, 11, 4), status="published", whole_month=False, scope=SCOPE
    )
    assert repo.week_rows == {(7, date(2026, 11, 2)): "published"}
    assert "fordelt" in message and "allerede over" not in message
    plan = await service.get_week_plan(date(2026, 11, 2), scope=SCOPE)
    assert plan.statuses[0].status == "published" and plan.statuses[0].explicit
    assert plan.statuses[0].can_open


@pytest.mark.asyncio
async def test_old_allocating_rows_show_as_open_and_cannot_be_set() -> None:
    repo = FakeRepository()
    repo.week_rows[(7, date(2026, 11, 2))] = "allocating"
    service = ShiftsService(repo, now=_clock(date(2026, 10, 28)))
    status = (await service.get_week_plan(date(2026, 11, 2), scope=SCOPE)).statuses[0]
    assert (status.status, status.label) == ("open", "Åpen for ønsker")
    with pytest.raises(ShiftPlacementError, match="Ugyldig status"):
        await service.set_week_status(
            group_id=7, week=date(2026, 11, 2), status="allocating", whole_month=False, scope=SCOPE
        )


@pytest.mark.asyncio
async def test_current_and_past_weeks_cannot_be_opened_for_wishes() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo, now=_clock(date(2026, 11, 4)))
    for week in (date(2026, 11, 2), date(2026, 11, 6), date(2026, 10, 26)):
        with pytest.raises(ShiftPlacementError, match="kan ikke åpnes"):
            await service.set_week_status(
                group_id=7, week=week, status="open", whole_month=False, scope=SCOPE
            )
    assert repo.week_rows == {}
    plan = await service.get_week_plan(date(2026, 11, 2), scope=SCOPE)
    assert not plan.statuses[0].can_open
    await service.set_week_status(
        group_id=7, week=date(2026, 11, 9), status="open", whole_month=False, scope=SCOPE
    )
    assert repo.week_rows == {(7, date(2026, 11, 9)): "open"}


@pytest.mark.asyncio
async def test_publish_month_writes_every_monday_of_that_month() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo, now=_clock(date(2026, 10, 28)))
    await service.set_week_status(
        group_id=7, week=date(2026, 11, 2), status="published", whole_month=True, scope=SCOPE
    )
    assert repo.week_rows == {
        (7, date(2026, 11, day)): "published" for day in (2, 9, 16, 23, 30)
    }


@pytest.mark.asyncio
async def test_past_week_can_change_with_a_note_and_bad_input_is_refused() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo, now=_clock(date(2026, 11, 18)))
    message = await service.set_week_status(
        group_id=7, week=date(2026, 11, 2), status="published", whole_month=False, scope=SCOPE
    )
    assert "Merk: uka er allerede over." in message
    with pytest.raises(ShiftPlacementError):
        await service.set_week_status(
            group_id=7, week=date(2026, 11, 2), status="nonsense", whole_month=False, scope=SCOPE
        )
    with pytest.raises(ShiftPlacementError):
        await service.set_week_status(
            group_id=7, week=date(2026, 11, 2), status="open", whole_month=True, scope=SCOPE
        )


@pytest.mark.asyncio
async def test_week_status_for_another_group_is_refused() -> None:
    repo = FakeRepository()
    with pytest.raises(ShiftForbiddenError, match="Grøndahls"):
        await ShiftsService(repo).set_week_status(
            group_id=8, week=date(2026, 11, 2), status="published", whole_month=False, scope=STJERNE_ONLY
        )
    assert repo.week_rows == {}


@pytest.mark.asyncio
async def test_banner_for_an_unpublished_week_and_none_once_published() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo, now=_clock(date(2026, 10, 28)))
    plan = await service.get_week_plan(date(2026, 10, 26), scope=SCOPE)
    assert len(plan.banners) == 1
    banner = plan.banners[0]
    assert banner.week_start == date(2026, 11, 2) and not banner.urgent
    assert banner.text == "Uke 45 er ikke publisert. Den blir åpen for alle mandag 2. november."
    urgent = ShiftsService(repo, now=_clock(date(2026, 10, 30)))
    assert (await urgent.get_week_plan(date(2026, 10, 26), scope=SCOPE)).banners[0].urgent
    await service.set_week_status(
        group_id=7, week=date(2026, 11, 2), status="published", whole_month=False, scope=SCOPE
    )
    assert (await service.get_week_plan(date(2026, 10, 26), scope=SCOPE)).banners == []
    far = ShiftsService(repo, now=_clock(date(2026, 10, 19)))
    repo.week_rows.clear()
    assert (await far.get_week_plan(date(2026, 10, 19), scope=SCOPE)).banners == []


@pytest.mark.asyncio
async def test_group_settings_are_saved_and_scoped() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo)
    listed = await service.list_group_settings(STJERNE_ONLY)
    assert [(g.name, g.is_subgroup) for g in listed] == [("Stjerne", True)]
    everything = await service.list_group_settings(ALL_GROUPS)
    # Skjenkegruppen takes part only through its subgroups, so it has no settings card.
    assert [(g.name, g.is_subgroup) for g in everything] == [
        ("Stjerne", True),
        ("Grøndahls", True),
    ]
    message = await service.save_group_settings(
        7,
        app_enabled=True,
        samf_enabled=False,
        meeting_point="  Ved baren ",
        courses_enabled=True,
        scope=STJERNE_ONLY,
    )
    assert repo.settings[7] == {
        "app_enabled": True,
        "samf_enabled": False,
        "meeting_point": "Ved baren",
        "courses_enabled": True,
    }
    assert "Skiftlista er på" in message
    with pytest.raises(ShiftForbiddenError):
        await service.save_group_settings(
            8, app_enabled=True, samf_enabled=True, meeting_point="", courses_enabled=False, scope=STJERNE_ONLY
        )
    # Not a Skjenke group: refused even for Admin.
    with pytest.raises(ShiftForbiddenError):
        await service.save_group_settings(
            20, app_enabled=True, samf_enabled=True, meeting_point="", courses_enabled=False, scope=ALL_GROUPS
        )
    assert 8 not in repo.settings and 20 not in repo.settings


# ------------------------------------------------- locks, closing and removing shifts


@pytest.mark.asyncio
async def test_assign_locks_the_shift_before_it_checks_the_place() -> None:
    repo = FakeRepository()
    await ShiftsService(repo).assign(slot_id=11, volunteer_id=101, scope=SCOPE)
    assert repo.events == [("lock", 1)]
    assert repo.added == [(11, 101)]


@pytest.mark.asyncio
async def test_move_takes_the_shift_lock_before_it_checks_the_new_place() -> None:
    repo = FakeRepository()
    repo.place(11, 100, 500)
    await ShiftsService(repo).move(assignment_id=500, slot_id=10, scope=SCOPE)
    # Both places are on shift 1, so it is locked once (ids are locked in order, without repeats).
    assert repo.events == [("lock", 1)]
    assert repo.moved == [(500, 10)]


@pytest.mark.asyncio
async def test_empty_shift_can_be_removed_but_one_with_people_cannot() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo)
    message = await service.remove_shift(shift_id=1, scope=SCOPE)
    assert "er fjernet" in message and ("delete", 1) in repo.events

    busy = FakeRepository()
    busy.place(11, 101, 500)
    with pytest.raises(ShiftPlacementError, match="Det står folk på skiftet"):
        await ShiftsService(busy).remove_shift(shift_id=1, scope=SCOPE)
    assert not any(event[0] == "delete" for event in busy.events)


@pytest.mark.asyncio
async def test_a_shift_with_people_can_be_closed_and_reopened() -> None:
    repo = FakeRepository()
    repo.place(11, 101, 500)
    service = ShiftsService(repo)
    assert "stengt" in await service.set_shift_closed(shift_id=1, closed=True, scope=SCOPE)
    assert repo.shift.is_closed
    with pytest.raises(ShiftPlacementError, match="stengt"):
        await service.assign(slot_id=10, volunteer_id=100, scope=SCOPE)
    assert "åpnet igjen" in await service.set_shift_closed(shift_id=1, closed=False, scope=SCOPE)
    assert not repo.shift.is_closed


@pytest.mark.asyncio
async def test_shift_removal_and_closing_respect_the_leaders_groups() -> None:
    repo = FakeRepository()
    service = ShiftsService(repo)
    other = GroupScope(frozenset({8}))
    with pytest.raises(ShiftForbiddenError, match="Stjerne"):
        await service.remove_shift(shift_id=1, scope=other)
    with pytest.raises(ShiftForbiddenError):
        await service.set_shift_closed(shift_id=1, closed=True, scope=other)
    with pytest.raises(ShiftNotFoundError):
        repo_missing = FakeRepository()

        async def none(shift_id):
            return None

        repo_missing.get_shift = none
        await ShiftsService(repo_missing).remove_shift(shift_id=5, scope=SCOPE)
    assert repo.events == []
