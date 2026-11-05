"""An in-memory repository for the week page's place actions (L2a).

The real ``ShiftsService`` runs on it, so the hard rules, the row lock and the "kan ikke i dag"
path are the real ones. Made-up people only. Week of Monday 2026-11-02 (Oslo), one bar (Stjerne, 7)
below a parent (1), a second bar (Kjøkken 8) and a group outside Skiftlista (20).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.shifts.models import (
    AssignedPerson,
    AssignmentRef,
    MemberRole,
    QualificationRecord,
    QualificationSettings,
    ShiftGroupOption,
    ShiftRecord,
    SlotRecord,
    SuggestionRecord,
)

TUESDAY = date(2026, 11, 3)
# Ids used by the tests.
SKIFTLEDER_SLOT, SKJENKER_SLOT, OPPLAERING_SLOT = 10, 11, 12
SECOND_SHIFT_SLOT = 21
SIGRID, SANDER, TORA, UNDINE, HALLE = 100, 101, 102, 103, 104


class PlacingRepository:
    def __init__(self) -> None:
        start = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)  # 17:00 Oslo
        self.shifts: dict[int, ShiftRecord] = {
            1: ShiftRecord(1, 7, "Stjerne", "Stjernesalen", "Dagskift", start, start.replace(hour=22), None, False, False, [
                SlotRecord(SKIFTLEDER_SLOT, "skiftleder", 1, False, 0, []),
                SlotRecord(SKJENKER_SLOT, "skjenker", 2, False, 1, []),
            ]),
            2: ShiftRecord(2, 7, "Stjerne", "Stjernesalen", "Kveldsskift", start.replace(hour=19), start.replace(hour=23), None, False, False, [
                SlotRecord(SECOND_SHIFT_SLOT, "skjenker", 2, False, 0, []),
            ]),
        }
        self.members = [
            MemberRole(SIGRID, "Sigrid Skiftleder", 7, "Stjerne", None),
            MemberRole(SANDER, "Sander Skjenker", 7, "Stjerne", None),
            MemberRole(TORA, "Tora Trainee", 7, "Stjerne", None),
            MemberRole(UNDINE, "Undine Under", 7, "Stjerne", None),
            MemberRole(HALLE, "Halle Hjelper", 7, "Stjerne", None),
        ]
        self.outsiders = {900: "Olga Utenfor"}
        self.qualifications = [
            (SIGRID, "skiftleder", "approved", 7),
            (SIGRID, "skjenker", "approved", 7),
            (SANDER, "skjenker", "approved", 7),
            (TORA, "skjenker", "training", 7),
            (UNDINE, "skjenker", "approved", 7),
            (HALLE, "skjenker", "approved", 7),
        ]
        self.wishes: dict[tuple[int, int], str] = {}
        self.birth_dates: dict[int, date | None] = {}
        self.assignments: dict[int, AssignmentRef] = {}
        self._next = 500
        self.events: list[tuple] = []
        self.admin_group_ids = [7]
        self.member_reads = 0
        self.suggestions: list[SuggestionRecord] = []
        self.training_targets: dict[tuple[int, str, int], int] = {}
        # The courses of the qualifications exist; Brannkurs is for everyone and is never touched.
        self.courses_by_name: dict[str, int] = {"Skjenkekurs": 41, "Skiftlederkurs": 42, "Brannkurs": 43}
        self.completed_courses: dict[int, set[int]] = {}
        self._suggestion_id = 0
        self.statuses: dict[tuple[int, date], str] = {}
        self.changes: dict[tuple[int, date], tuple[datetime, str]] = {}

    # ---- test helpers
    def shift_of(self, slot_id: int) -> ShiftRecord:
        return next(s for s in self.shifts.values() if any(x.slot_id == slot_id for x in s.slots))

    def place(self, slot_id: int, volunteer_id: int, *, absent: bool = False, swap: bool = False) -> int:
        shift = self.shift_of(slot_id)
        slot = next(x for x in shift.slots if x.slot_id == slot_id)
        self._next += 1
        name = next((m.name for m in self.members if m.volunteer_id == volunteer_id), "x")
        slot.people.append(AssignedPerson(self._next, volunteer_id, name, swap, cannot_attend=absent))
        self.assignments[self._next] = AssignmentRef(self._next, slot_id, shift.shift_id, volunteer_id)
        return self._next

    def who(self, slot_id: int) -> list[int]:
        slot = next(x for s in self.shifts.values() for x in s.slots if x.slot_id == slot_id)
        return [p.volunteer_id for p in slot.people]

    def suggest(self, slot_id: int, volunteer_id: int, why: str = "Valgt fordi han vil") -> int:
        self._suggestion_id += 1
        shift = self.shift_of(slot_id)
        self.suggestions.append(SuggestionRecord(self._suggestion_id, slot_id, shift.shift_id, volunteer_id, why))
        return self._suggestion_id

    # ---- the repository
    async def list_shift_groups(self):
        return [ShiftGroupOption(7, "Stjerne"), ShiftGroupOption(8, "Kjøkken")]

    def _copy(self, shift: ShiftRecord) -> ShiftRecord:
        return ShiftRecord(
            shift.shift_id, shift.group_id, shift.group_name, shift.venue, shift.title, shift.starts_at,
            shift.ends_at, shift.note, shift.is_closed, shift.training_replaces_slot,
            [SlotRecord(s.slot_id, s.kind, s.count, s.open_for_samf, s.sort_order,
                        [AssignedPerson(p.assignment_id, p.volunteer_id, p.name, p.swap_requested, p.cannot_attend)
                         for p in s.people]) for s in shift.slots],
        )

    async def get_slot_shift_id(self, slot_id):
        return next((s.shift_id for s in self.shifts.values() if any(x.slot_id == slot_id for x in s.slots)), None)

    async def get_shift(self, shift_id):
        return self._copy(self.shifts[shift_id]) if shift_id in self.shifts else None

    async def list_shifts_between(self, starts_from, starts_before, group_id=None, *, group_ids=None):
        found = [self._copy(s) for s in self.shifts.values() if starts_from <= s.starts_at < starts_before]
        if group_ids is not None:
            found = [s for s in found if s.group_id in group_ids]
        if group_id is not None:
            found = [s for s in found if s.group_id == group_id]
        return found

    async def count_shifts_by_volunteer(self, starts_from, starts_before):
        counts: dict[int, int] = {}
        for shift in self.shifts.values():
            if starts_from <= shift.starts_at < starts_before:
                for vid in {p.volunteer_id for s in shift.slots for p in s.people}:
                    counts[vid] = counts.get(vid, 0) + 1
        return counts

    async def list_shift_members(self, semester, group_ids):
        self.member_reads += 1
        return list(self.members)

    async def list_member_group_ids(self, volunteer_id, semester):
        return sorted({m.group_id for m in self.members if m.volunteer_id == volunteer_id})

    async def list_qualifications(self, volunteer_ids):
        return [QualificationRecord(v, q, st, g, None, False, None, None)
                for v, q, st, g in self.qualifications if v in volunteer_ids]

    async def list_group_qualifications(self, group_ids):
        started = datetime(2026, 10, 1, tzinfo=UTC)
        return [QualificationRecord(v, q, st, g, self.training_targets.get((v, q, g)), False, None, None, started)
                for v, q, st, g in self.qualifications if g in group_ids]

    async def list_attended(self, group_id, volunteer_ids, since):
        found = {}
        for shift in self.shifts.values():
            if shift.group_id != group_id or shift.starts_at < since:
                continue
            for slot in shift.slots:
                for person in slot.people:
                    if person.volunteer_id in volunteer_ids and not person.cannot_attend:
                        found.setdefault(person.volunteer_id, []).append((shift.starts_at, shift.ends_at))
        return found

    async def list_courses_by_name(self, names):
        return {name: self.courses_by_name[name] for name in names if name in self.courses_by_name}

    async def list_completed_courses(self, volunteer_ids, course_ids):
        return {v: {c for c in self.completed_courses.get(v, set()) if c in course_ids} for v in volunteer_ids}

    async def add_course_completion(self, volunteer_id, course_id, semester):
        self.completed_courses.setdefault(volunteer_id, set()).add(course_id)
        self.events.append(("course", volunteer_id, course_id, semester))

    async def start_training(self, volunteer_id, qualification, group_id):
        self.qualifications.append((volunteer_id, qualification, "training", group_id))

    async def set_training_target(self, volunteer_id, qualification, group_id, target):
        self.training_targets[(volunteer_id, qualification, group_id)] = target

    async def approve_training(self, volunteer_id, qualification, group_id, approved_by, now):
        self.qualifications = [
            (v, q, "approved" if (v, q, g) == (volunteer_id, qualification, group_id) else st, g)
            for v, q, st, g in self.qualifications
        ]

    async def stop_training(self, volunteer_id, qualification, group_id):
        self.qualifications = [
            row for row in self.qualifications
            if not (row[0] == volunteer_id and row[1] == qualification and row[3] == group_id and row[2] == "training")
        ]

    async def get_qualification_settings(self, group_ids):
        return {gid: QualificationSettings() for gid in group_ids}

    async def list_group_parents(self):
        return {7: 1, 8: 1, 1: None, 20: None}

    async def list_direct_participating_group_ids(self):
        return [7, 8]

    async def list_group_admin_group_ids(self, auth_user_id):
        return self.admin_group_ids

    async def list_wishes_for(self, shift_ids, volunteer_ids):
        return {k: v for k, v in self.wishes.items() if k[0] in shift_ids and k[1] in volunteer_ids}

    async def list_answered_volunteers(self, group_ids, starts_from, starts_before):
        return {7: {SANDER}}

    async def get_week_statuses(self, group_ids, weeks):
        return {key: value for key, value in self.statuses.items() if key[0] in group_ids and key[1] in weeks}

    async def set_week_statuses(self, group_id, weeks, status):
        self.events.append(("status", group_id, status))
        for week in weeks:
            self.statuses[(group_id, week)] = status
            self.changes.pop((group_id, week), None)

    async def get_week_changes(self, group_id, weeks):
        return {
            week: self.changes[(group_id, week)]
            for week in weeks
            if (group_id, week) in self.changes and self.statuses.get((group_id, week)) == "published"
        }

    async def mark_week_changed(self, group_id, week, by):
        if self.statuses.get((group_id, week)) == "published":
            self.changes[(group_id, week)] = (datetime.now(UTC), by)

    async def get_volunteer_name(self, volunteer_id):
        if volunteer_id in self.outsiders:
            return self.outsiders[volunteer_id]
        return next((m.name for m in self.members if m.volunteer_id == volunteer_id), None)

    async def get_volunteer_birth_date(self, volunteer_id):
        return self.birth_dates.get(volunteer_id)

    async def get_birth_dates(self, volunteer_ids):
        return {vid: self.birth_dates.get(vid) for vid in volunteer_ids}

    async def get_assignment(self, assignment_id):
        return self.assignments.get(assignment_id)

    async def add_assignment(self, slot_id, volunteer_id):
        self.events.append(("add", slot_id, volunteer_id))
        return self.place(slot_id, volunteer_id)

    async def remove_assignment(self, assignment_id):
        self.events.append(("remove", assignment_id))
        ref = self.assignments.pop(assignment_id)
        for shift in self.shifts.values():
            for slot in shift.slots:
                slot.people = [p for p in slot.people if p.assignment_id != ref.assignment_id]

    async def remove_assignment_from_slot(self, assignment_id, slot_id):
        ref = self.assignments.get(assignment_id)
        if ref is None or ref.slot_id != slot_id:
            return False
        await self.remove_assignment(assignment_id)
        return True

    async def list_suggestions(self, group_id, starts_from, starts_before):
        found = []
        for record in self.suggestions:
            shift = self.shifts.get(record.shift_id)
            if shift and shift.group_id == group_id and starts_from <= shift.starts_at < starts_before:
                found.append(record)
        return sorted(found, key=lambda r: r.suggestion_id)

    async def replace_suggestions(self, shift_ids, rows):
        self.suggestions = [r for r in self.suggestions if r.shift_id not in shift_ids]
        for slot_id, volunteer_id, why in rows:
            self.suggest(slot_id, volunteer_id, why)

    async def delete_suggestions(self, suggestion_ids):
        self.suggestions = [r for r in self.suggestions if r.suggestion_id not in suggestion_ids]

    async def lock_week(self, group_id, week):
        self.events.append(("lock_week", group_id, week))

    async def move_assignment(self, assignment_id, slot_id):
        self.events.append(("move", assignment_id, slot_id))
        ref = self.assignments[assignment_id]
        person = None
        for shift in self.shifts.values():
            for slot in shift.slots:
                found = [p for p in slot.people if p.assignment_id == assignment_id]
                if found:
                    person = found[0]
                    slot.people = [p for p in slot.people if p.assignment_id != assignment_id]
        target = self.shift_of(slot_id)
        person.swap_requested = False
        person.cannot_attend = False
        next(x for x in target.slots if x.slot_id == slot_id).people.append(person)
        self.assignments[assignment_id] = AssignmentRef(assignment_id, slot_id, target.shift_id, ref.volunteer_id)

    async def set_slot_count(self, slot_id, count):
        self.events.append(("slot_count", slot_id, count))
        next(x for s in self.shifts.values() for x in s.slots if x.slot_id == slot_id).count = count

    async def add_slot(self, shift_id, kind, count, sort_order):
        self.events.append(("add_slot", shift_id, kind, count))
        self._next += 1
        self.shifts[shift_id].slots.append(SlotRecord(self._next, kind, count, False, sort_order, []))
        return self._next

    async def delete_slot(self, slot_id):
        self.events.append(("delete_slot", slot_id))
        for shift in self.shifts.values():
            shift.slots = [x for x in shift.slots if x.slot_id != slot_id]

    async def lock_shift(self, shift_id):
        self.events.append(("lock", shift_id))

    async def get_group_name(self, group_id):
        return {7: "Stjerne", 8: "Kjøkken", 1: "Skjenkegruppen"}.get(group_id)
