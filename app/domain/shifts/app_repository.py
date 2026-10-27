"""Data access for Skiftlista in the app (on top of the leader repository).

Course sessions and sign-ups are owned by the courses module; the shifts
module writes sign-ups here because signing up is part of claiming a
place ("meld meg på brannkurs og ta skiftet" in one step).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, delete, func, insert, select, update

from app.domain.courses.tables import (
    course_completions,
    course_session_signups,
    course_sessions,
    courses,
)
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.models import ShiftRecord
from app.domain.shifts.repository import ShiftsRepository, _aware, _shift_select
from app.domain.shifts.tables import (
    shift_assignments,
    shift_availability_rules,
    shift_group_settings,
    shift_week_status,
    shift_slots,
    shift_templates,
    shift_wishes,
    shifts,
)
from app.domain.volunteers.tables import volunteer_records


@dataclass(frozen=True, slots=True)
class PersonRow:
    volunteer_id: int
    first_name: str
    birth_date: date | None


@dataclass(frozen=True, slots=True)
class GroupSetting:
    app_enabled: bool
    samf_enabled: bool
    meeting_point: str | None


@dataclass(frozen=True, slots=True)
class AssignmentState:
    assignment_id: int
    slot_id: int
    shift_id: int
    volunteer_id: int
    swap_requested: bool
    cannot_attend: bool


@dataclass(frozen=True, slots=True)
class SessionRow:
    session_id: int
    course_id: int
    starts_at: datetime
    location: str | None
    capacity: int
    is_cancelled: bool
    signed_up: int

    @property
    def spots_left(self) -> int:
        return max(0, self.capacity - self.signed_up)


@dataclass(frozen=True, slots=True)
class SignupRow:
    session_id: int
    course_id: int
    starts_at: datetime


class ShiftsAppRepository(ShiftsRepository):
    # ------------------------------------------------------------ people and groups

    async def get_person(self, volunteer_id: int) -> PersonRow | None:
        row = await self.fetch_first_mapping(
            select(
                volunteer_records.c.id,
                volunteer_records.c.first_name,
                volunteer_records.c.birth_date,
            ).where(volunteer_records.c.id == volunteer_id)
        )
        if row is None:
            return None
        return PersonRow(
            volunteer_id=row["id"],
            first_name=(row["first_name"] or "").strip(),
            birth_date=row["birth_date"],
        )

    async def list_person_roles(self, volunteer_id: int, semester: int) -> list[tuple[int, str | None]]:
        """(group id, verv name) for every role assignment this semester."""
        rows = await self.fetch_all_mappings(
            select(role_assignments.c.group_id, assignment_roles.c.name.label("role_name"))
            .select_from(
                role_assignments.outerjoin(
                    assignment_roles, assignment_roles.c.id == role_assignments.c.role_id
                )
            )
            .where(
                role_assignments.c.volunteer_id == volunteer_id,
                role_assignments.c.semester == semester,
            )
        )
        return [(row["group_id"], row["role_name"]) for row in rows]

    async def list_group_settings(self) -> dict[int, GroupSetting]:
        rows = await self.fetch_all_mappings(
            select(
                shift_group_settings.c.group_id,
                shift_group_settings.c.app_enabled,
                shift_group_settings.c.samf_enabled,
                shift_group_settings.c.meeting_point,
            )
        )
        return {
            row["group_id"]: GroupSetting(
                app_enabled=row["app_enabled"],
                samf_enabled=row["samf_enabled"],
                meeting_point=row["meeting_point"],
            )
            for row in rows
        }

    async def list_template_weekdays(self, group_ids: list[int]) -> set[int]:
        if not group_ids:
            return set()
        rows = await self.fetch_scalars_all(
            select(shift_templates.c.weekday)
            .where(
                shift_templates.c.group_id.in_(group_ids),
                shift_templates.c.is_active.is_(True),
            )
            .distinct()
        )
        return {int(weekday) for weekday in rows}

    # ------------------------------------------------------------ shifts

    async def list_volunteer_shifts(
        self, volunteer_id: int, starts_from: datetime, starts_before: datetime | None = None
    ) -> list[ShiftRecord]:
        shift_ids = (
            select(shift_slots.c.shift_id)
            .select_from(
                shift_assignments.join(shift_slots, shift_slots.c.id == shift_assignments.c.slot_id)
            )
            .where(shift_assignments.c.volunteer_id == volunteer_id)
        )
        stmt = (
            _shift_select()
            .where(shifts.c.id.in_(shift_ids), shifts.c.starts_at >= starts_from)
            .order_by(shifts.c.starts_at.asc(), shifts.c.id.asc())
        )
        if starts_before is not None:
            stmt = stmt.where(shifts.c.starts_at < starts_before)
        return await self._load_shifts(stmt)

    async def lock_shift(self, shift_id: int) -> None:
        """Hold the shift row until commit so two claims cannot fill one place."""
        await self.fetch_scalar(select(shifts.c.id).where(shifts.c.id == shift_id).with_for_update())

    async def week_statuses(self, group_ids: list[int], weeks: list[date]) -> dict[tuple[int, date], str]:
        if not group_ids or not weeks:
            return {}
        rows = await self.fetch_all_mappings(
            select(
                shift_week_status.c.group_id,
                shift_week_status.c.week_start,
                shift_week_status.c.status,
            ).where(
                shift_week_status.c.group_id.in_(group_ids),
                shift_week_status.c.week_start.in_(weeks),
            )
        )
        return {(row["group_id"], _as_date(row["week_start"])): row["status"] for row in rows}

    async def get_assignment_state(self, assignment_id: int) -> AssignmentState | None:
        row = await self.fetch_first_mapping(
            select(
                shift_assignments.c.id,
                shift_assignments.c.slot_id,
                shift_assignments.c.volunteer_id,
                shift_assignments.c.swap_requested,
                shift_assignments.c.cannot_attend,
                shift_slots.c.shift_id,
            )
            .select_from(
                shift_assignments.join(shift_slots, shift_slots.c.id == shift_assignments.c.slot_id)
            )
            .where(shift_assignments.c.id == assignment_id)
        )
        if row is None:
            return None
        return AssignmentState(
            assignment_id=row["id"],
            slot_id=row["slot_id"],
            shift_id=row["shift_id"],
            volunteer_id=row["volunteer_id"],
            swap_requested=row["swap_requested"],
            cannot_attend=row["cannot_attend"],
        )

    async def set_assignment_status(
        self,
        assignment_id: int,
        *,
        swap_requested: bool,
        cannot_attend: bool,
        message: str | None,
        changed_at: datetime,
    ) -> None:
        await self.execute(
            update(shift_assignments)
            .where(shift_assignments.c.id == assignment_id)
            .values(
                swap_requested=swap_requested,
                cannot_attend=cannot_attend,
                status_message=message,
                status_changed_at=changed_at,
            )
        )

    # ------------------------------------------------------------ wishes

    async def list_rules(self, volunteer_id: int, semester: int) -> dict[int, str]:
        rows = await self.fetch_all_mappings(
            select(shift_availability_rules.c.weekday, shift_availability_rules.c.answer).where(
                shift_availability_rules.c.volunteer_id == volunteer_id,
                shift_availability_rules.c.semester == semester,
            )
        )
        return {int(row["weekday"]): row["answer"] for row in rows}

    async def set_rule(self, volunteer_id: int, semester: int, weekday: int, answer: str | None) -> None:
        await self.execute(
            delete(shift_availability_rules).where(
                shift_availability_rules.c.volunteer_id == volunteer_id,
                shift_availability_rules.c.semester == semester,
                shift_availability_rules.c.weekday == weekday,
            )
        )
        if answer is not None:
            await self.execute(
                insert(shift_availability_rules).values(
                    volunteer_id=volunteer_id,
                    semester=semester,
                    weekday=weekday,
                    answer=answer,
                    updated_at=func.now(),
                )
            )

    async def list_wishes(self, volunteer_id: int, shift_ids: list[int]) -> dict[int, str]:
        if not shift_ids:
            return {}
        rows = await self.fetch_all_mappings(
            select(shift_wishes.c.shift_id, shift_wishes.c.answer).where(
                shift_wishes.c.volunteer_id == volunteer_id,
                shift_wishes.c.shift_id.in_(shift_ids),
            )
        )
        return {row["shift_id"]: row["answer"] for row in rows}

    async def set_wish(self, volunteer_id: int, shift_id: int, answer: str | None) -> None:
        await self.execute(
            delete(shift_wishes).where(
                shift_wishes.c.volunteer_id == volunteer_id,
                shift_wishes.c.shift_id == shift_id,
            )
        )
        if answer is not None:
            await self.execute(
                insert(shift_wishes).values(
                    volunteer_id=volunteer_id,
                    shift_id=shift_id,
                    answer=answer,
                    updated_at=func.now(),
                )
            )

    # ------------------------------------------------------------ courses

    async def find_courses(self, names: list[str]) -> dict[str, int]:
        """Lower-cased course name to course id, for the names that exist."""
        wanted = [name.lower() for name in names]
        rows = await self.fetch_all_mappings(
            select(courses.c.id, courses.c.name).where(func.lower(courses.c.name).in_(wanted))
        )
        found: dict[str, int] = {}
        for row in sorted(rows, key=lambda item: item["id"]):
            found.setdefault(row["name"].lower(), row["id"])
        return found

    async def latest_completions(self, volunteer_id: int, course_ids: list[int]) -> dict[int, int]:
        if not course_ids:
            return {}
        rows = await self.fetch_all_mappings(
            select(
                course_completions.c.course_id,
                func.max(course_completions.c.completed_semester).label("semester"),
            )
            .where(
                course_completions.c.volunteer_id == volunteer_id,
                course_completions.c.course_id.in_(course_ids),
            )
            .group_by(course_completions.c.course_id)
        )
        return {row["course_id"]: int(row["semester"]) for row in rows}

    async def list_sessions(
        self, course_ids: list[int], starts_after: datetime, starts_before: datetime
    ) -> list[SessionRow]:
        if not course_ids:
            return []
        return await self._sessions(
            and_(
                course_sessions.c.course_id.in_(course_ids),
                course_sessions.c.starts_at > starts_after,
                course_sessions.c.starts_at < starts_before,
                course_sessions.c.is_cancelled.is_(False),
            )
        )

    async def get_session(self, session_id: int) -> SessionRow | None:
        found = await self._sessions(course_sessions.c.id == session_id)
        return found[0] if found else None

    async def lock_session(self, session_id: int) -> None:
        await self.fetch_scalar(
            select(course_sessions.c.id).where(course_sessions.c.id == session_id).with_for_update()
        )

    async def _sessions(self, condition) -> list[SessionRow]:
        counts = (
            select(
                course_session_signups.c.session_id,
                func.count().label("signed_up"),
            )
            .group_by(course_session_signups.c.session_id)
            .subquery()
        )
        rows = await self.fetch_all_mappings(
            select(
                course_sessions.c.id,
                course_sessions.c.course_id,
                course_sessions.c.starts_at,
                course_sessions.c.location,
                course_sessions.c.capacity,
                course_sessions.c.is_cancelled,
                func.coalesce(counts.c.signed_up, 0).label("signed_up"),
            )
            .select_from(course_sessions.outerjoin(counts, counts.c.session_id == course_sessions.c.id))
            .where(condition)
            .order_by(course_sessions.c.starts_at.asc(), course_sessions.c.id.asc())
        )
        return [
            SessionRow(
                session_id=row["id"],
                course_id=row["course_id"],
                starts_at=_aware(row["starts_at"]),
                location=row["location"],
                capacity=row["capacity"],
                is_cancelled=row["is_cancelled"],
                signed_up=int(row["signed_up"]),
            )
            for row in rows
        ]

    async def list_signups(
        self, volunteer_id: int, course_ids: list[int], starts_after: datetime
    ) -> list[SignupRow]:
        if not course_ids:
            return []
        rows = await self.fetch_all_mappings(
            select(
                course_sessions.c.id,
                course_sessions.c.course_id,
                course_sessions.c.starts_at,
            )
            .select_from(
                course_session_signups.join(
                    course_sessions, course_sessions.c.id == course_session_signups.c.session_id
                )
            )
            .where(
                course_session_signups.c.volunteer_id == volunteer_id,
                course_sessions.c.course_id.in_(course_ids),
                course_sessions.c.starts_at > starts_after,
                course_sessions.c.is_cancelled.is_(False),
            )
            .order_by(course_sessions.c.starts_at.asc())
        )
        return [
            SignupRow(
                session_id=row["id"],
                course_id=row["course_id"],
                starts_at=_aware(row["starts_at"]),
            )
            for row in rows
        ]

    async def add_signup(self, session_id: int, volunteer_id: int) -> None:
        await self.execute(
            insert(course_session_signups).values(session_id=session_id, volunteer_id=volunteer_id)
        )

    async def remove_signup(self, session_id: int, volunteer_id: int) -> None:
        await self.execute(
            delete(course_session_signups).where(
                course_session_signups.c.session_id == session_id,
                course_session_signups.c.volunteer_id == volunteer_id,
            )
        )


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
