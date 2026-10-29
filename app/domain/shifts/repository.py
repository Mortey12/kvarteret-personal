from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID

from sqlalchemy import delete, func, insert, select, update

from app.db.repository import SqlAlchemyRepository
from app.domain.admin_accounts.tables import group_admin_memberships
from app.domain.courses.tables import courses
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.planning import OSLO
from app.domain.shifts.models import (
    ClosedPeriod,
    PlannedShift,
    ShiftTemplate,
    TemplateSlot,
    AssignedPerson,
    AssignmentRef,
    MemberRole,
    QualificationRecord,
    QualificationSettings,
    ShiftGroupOption,
    ShiftRecord,
    SlotRecord,
)
from app.domain.shifts.tables import (
    shift_assignments,
    shift_closed_periods,
    shift_template_slots,
    shift_group_settings,
    shift_slots,
    shift_templates,
    shift_availability_rules,
    shift_week_status,
    shift_wishes,
    shifts,
    volunteer_qualifications,
)
from app.domain.volunteers.tables import volunteer_records
from app.shared.coercion import require_datetime
from app.shared.text import build_full_name


class ShiftsRepository(SqlAlchemyRepository):
    async def list_shift_groups(self) -> list[ShiftGroupOption]:
        """Groups that have shift templates, for the filter on the plan."""
        stmt = (
            select(groups.c.id, groups.c.name)
            .where(
                groups.c.id.in_(select(shift_templates.c.group_id).distinct()),
                groups.c.is_active.is_(True),
            )
            .order_by(groups.c.name.asc())
        )
        rows = await self.fetch_all_mappings(stmt)
        return [ShiftGroupOption(group_id=row["id"], name=row["name"]) for row in rows]

    async def list_group_admin_group_ids(self, auth_user_id: UUID) -> list[int]:
        """Active groups assigned to a Gruppeadmin (group_admin_memberships)."""
        rows = await self.fetch_all_mappings(
            select(groups.c.id)
            .join(group_admin_memberships, group_admin_memberships.c.group_id == groups.c.id)
            .where(group_admin_memberships.c.auth_user_id == auth_user_id, groups.c.is_active.is_(True))
        )
        return [row["id"] for row in rows]

    async def list_group_parents(self) -> dict[int, int | None]:
        rows = await self.fetch_all_mappings(
            select(groups.c.id, groups.c.parent_group_id).where(groups.c.is_active.is_(True))
        )
        return {row["id"]: row["parent_group_id"] for row in rows}

    async def list_direct_participating_group_ids(self) -> list[int]:
        """Active groups with a settings row or shift templates."""
        with_settings = select(shift_group_settings.c.group_id)
        with_templates = select(shift_templates.c.group_id)
        return await self.fetch_scalars_all(
            select(groups.c.id).where(
                groups.c.is_active.is_(True),
                groups.c.id.in_(with_settings.union(with_templates)),
            )
        )

    async def get_group_name(self, group_id: int) -> str | None:
        return await self.fetch_scalar(select(groups.c.name).where(groups.c.id == group_id))

    async def list_groups_with_settings(self) -> list[dict]:
        """Every active group with its Skiftlista switches (defaults when no row)."""
        return await self.fetch_all_mappings(
            select(
                groups.c.id,
                groups.c.name,
                groups.c.parent_group_id,
                shift_group_settings.c.app_enabled,
                shift_group_settings.c.samf_enabled,
                shift_group_settings.c.meeting_point,
                shift_group_settings.c.courses_enabled,
            )
            .select_from(
                groups.outerjoin(shift_group_settings, shift_group_settings.c.group_id == groups.c.id)
            )
            .where(groups.c.is_active.is_(True))
            .order_by(groups.c.name.asc())
        )

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

    async def save_group_settings(
        self,
        group_id: int,
        *,
        app_enabled: bool,
        samf_enabled: bool,
        meeting_point: str | None,
        courses_enabled: bool,
    ) -> None:
        values = {
            "app_enabled": app_enabled,
            "samf_enabled": samf_enabled,
            "meeting_point": meeting_point,
            "courses_enabled": courses_enabled,
            "updated_at": datetime.now(UTC),
        }
        result = await self.session.execute(
            update(shift_group_settings).where(shift_group_settings.c.group_id == group_id).values(**values)
        )
        if result.rowcount == 0:
            await self.session.execute(insert(shift_group_settings).values(group_id=group_id, **values))

    async def get_week_statuses(self, group_ids: list[int], weeks: list[date]) -> dict[tuple[int, date], str]:
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

    async def set_week_statuses(self, group_id: int, weeks: list[date], status: str) -> None:
        """Explicit rows, one per Monday in ``weeks``."""
        now = datetime.now(UTC)
        for week in weeks:
            result = await self.session.execute(
                update(shift_week_status)
                .where(shift_week_status.c.group_id == group_id, shift_week_status.c.week_start == week)
                .values(status=status, updated_at=now)
            )
            if result.rowcount == 0:
                await self.session.execute(
                    insert(shift_week_status).values(
                        group_id=group_id, week_start=week, status=status, updated_at=now
                    )
                )

    async def list_shift_group_weeks(
        self, starts_from: datetime, starts_before: datetime, group_ids: list[int]
    ) -> set[tuple[int, date]]:
        """(group id, Oslo date of the start) for shifts in the range."""
        if not group_ids:
            return set()
        rows = await self.fetch_all_mappings(
            select(shifts.c.group_id, shifts.c.starts_at).where(
                shifts.c.starts_at >= starts_from,
                shifts.c.starts_at < starts_before,
                shifts.c.group_id.in_(group_ids),
            )
        )
        return {(row["group_id"], _aware(row["starts_at"]).astimezone(OSLO).date()) for row in rows}

    async def list_shifts_between(
        self,
        starts_from: datetime,
        starts_before: datetime,
        group_id: int | None = None,
        *,
        group_ids: list[int] | None = None,
    ) -> list[ShiftRecord]:
        shift_stmt = (
            _shift_select()
            .where(shifts.c.starts_at >= starts_from, shifts.c.starts_at < starts_before)
            .order_by(shifts.c.starts_at.asc(), shifts.c.id.asc())
        )
        if group_id is not None:
            shift_stmt = shift_stmt.where(shifts.c.group_id == group_id)
        if group_ids is not None:
            if not group_ids:
                return []
            shift_stmt = shift_stmt.where(shifts.c.group_id.in_(group_ids))
        return await self._load_shifts(shift_stmt)

    async def get_shift(self, shift_id: int) -> ShiftRecord | None:
        found = await self._load_shifts(_shift_select().where(shifts.c.id == shift_id))
        return found[0] if found else None

    async def _load_shifts(self, shift_stmt) -> list[ShiftRecord]:
        shift_rows = await self.fetch_all_mappings(shift_stmt)
        if not shift_rows:
            return []

        records: dict[int, ShiftRecord] = {
            row["id"]: ShiftRecord(
                shift_id=row["id"],
                group_id=row["group_id"],
                group_name=row["group_name"],
                venue=row["venue"],
                title=row["title"],
                starts_at=_aware(row["starts_at"]),
                ends_at=_aware(row["ends_at"]),
                note=row["note"],
                is_closed=row["is_closed"],
                training_replaces_slot=row["training_replaces_slot"],
            )
            for row in shift_rows
        }

        slot_rows = await self.fetch_all_mappings(
            select(
                shift_slots.c.id,
                shift_slots.c.shift_id,
                shift_slots.c.kind,
                shift_slots.c.count,
                shift_slots.c.open_for_samf,
                shift_slots.c.sort_order,
            )
            .where(shift_slots.c.shift_id.in_(list(records)))
            .order_by(shift_slots.c.sort_order.asc(), shift_slots.c.id.asc())
        )
        slots: dict[int, SlotRecord] = {}
        for row in slot_rows:
            slot = SlotRecord(
                slot_id=row["id"],
                kind=row["kind"],
                count=row["count"],
                open_for_samf=row["open_for_samf"],
                sort_order=row["sort_order"],
            )
            slots[slot.slot_id] = slot
            records[row["shift_id"]].slots.append(slot)

        if slots:
            people_rows = await self.fetch_all_mappings(
                select(
                    shift_assignments.c.id,
                    shift_assignments.c.slot_id,
                    shift_assignments.c.volunteer_id,
                    shift_assignments.c.swap_requested,
                    shift_assignments.c.cannot_attend,
                    volunteer_records.c.first_name,
                    volunteer_records.c.last_name,
                )
                .select_from(
                    shift_assignments.join(
                        volunteer_records,
                        volunteer_records.c.id == shift_assignments.c.volunteer_id,
                    )
                )
                .where(shift_assignments.c.slot_id.in_(list(slots)))
                .order_by(shift_assignments.c.created_at.asc(), shift_assignments.c.id.asc())
            )
            for row in people_rows:
                slots[row["slot_id"]].people.append(
                    AssignedPerson(
                        assignment_id=row["id"],
                        volunteer_id=row["volunteer_id"],
                        name=build_full_name(row["first_name"], row["last_name"]),
                        swap_requested=row["swap_requested"],
                        cannot_attend=row["cannot_attend"],
                        first_name=(row["first_name"] or "").strip(),
                    )
                )
        return list(records.values())


    async def get_slot_shift_id(self, slot_id: int) -> int | None:
        return await self.fetch_scalar(
            select(shift_slots.c.shift_id).where(shift_slots.c.id == slot_id)
        )

    async def get_assignment(self, assignment_id: int) -> AssignmentRef | None:
        row = await self.fetch_first_mapping(
            select(
                shift_assignments.c.id,
                shift_assignments.c.slot_id,
                shift_assignments.c.volunteer_id,
                shift_slots.c.shift_id,
            )
            .select_from(
                shift_assignments.join(shift_slots, shift_slots.c.id == shift_assignments.c.slot_id)
            )
            .where(shift_assignments.c.id == assignment_id)
        )
        if row is None:
            return None
        return AssignmentRef(
            assignment_id=row["id"],
            slot_id=row["slot_id"],
            shift_id=row["shift_id"],
            volunteer_id=row["volunteer_id"],
        )

    async def list_shift_members(self, semester: int, group_ids: list[int]) -> list[MemberRole]:
        """Members of the given groups this semester, with their verv."""
        if not group_ids:
            return []
        rows = await self.fetch_all_mappings(
            select(
                volunteer_records.c.id.label("volunteer_id"),
                volunteer_records.c.first_name,
                volunteer_records.c.last_name,
                groups.c.id.label("group_id"),
                groups.c.name.label("group_name"),
                assignment_roles.c.name.label("role_name"),
            )
            .select_from(
                role_assignments.join(
                    volunteer_records, volunteer_records.c.id == role_assignments.c.volunteer_id
                )
                .join(groups, groups.c.id == role_assignments.c.group_id)
                .outerjoin(assignment_roles, assignment_roles.c.id == role_assignments.c.role_id)
            )
            .where(
                role_assignments.c.semester == semester,
                role_assignments.c.group_id.in_(group_ids),
            )
            .order_by(volunteer_records.c.first_name.asc(), volunteer_records.c.last_name.asc())
        )
        return [
            MemberRole(
                volunteer_id=row["volunteer_id"],
                name=build_full_name(row["first_name"], row["last_name"]),
                group_id=row["group_id"],
                group_name=row["group_name"],
                role_name=row["role_name"],
            )
            for row in rows
        ]

    async def list_qualifications(self, volunteer_ids: list[int]) -> list[QualificationRecord]:
        """Qualification rows (training and approved) of the given people."""
        if not volunteer_ids:
            return []
        return await self._load_qualifications(
            volunteer_qualifications.c.volunteer_id.in_(volunteer_ids)
        )

    async def list_group_qualifications(self, group_ids: list[int]) -> list[QualificationRecord]:
        """Qualification rows whose bar is one of the given groups."""
        if not group_ids:
            return []
        return await self._load_qualifications(volunteer_qualifications.c.group_id.in_(group_ids))

    async def _load_qualifications(self, condition) -> list[QualificationRecord]:
        rows = await self.fetch_all_mappings(
            select(volunteer_qualifications)
            .where(condition)
            .order_by(
                volunteer_qualifications.c.volunteer_id.asc(),
                volunteer_qualifications.c.qualification.asc(),
                volunteer_qualifications.c.group_id.asc(),
            )
        )
        return [
            QualificationRecord(
                volunteer_id=row["volunteer_id"],
                qualification=row["qualification"],
                status=row["status"],
                group_id=row["group_id"],
                training_target=row["training_target"],
                takes_trainees=row["takes_trainees"],
                approved_by=row["approved_by"],
                approved_at=row["approved_at"],
            )
            for row in rows
        ]

    async def get_qualification_settings(
        self, group_ids: list[int]
    ) -> dict[int, QualificationSettings]:
        """Training and sibling settings per group; defaults when a group has no row."""
        if not group_ids:
            return {}
        rows = await self.fetch_all_mappings(
            select(
                shift_group_settings.c.group_id,
                shift_group_settings.c.training_shifts_needed,
                shift_group_settings.c.sibling_skiftleder_allowed,
            ).where(shift_group_settings.c.group_id.in_(group_ids))
        )
        found = {
            row["group_id"]: QualificationSettings(
                training_shifts_needed=row["training_shifts_needed"],
                sibling_skiftleder_allowed=row["sibling_skiftleder_allowed"],
            )
            for row in rows
        }
        return {group_id: found.get(group_id, QualificationSettings()) for group_id in group_ids}

    async def list_wishes_for(
        self, shift_ids: list[int], volunteer_ids: list[int]
    ) -> dict[tuple[int, int], str]:
        """Per-shift answers, keyed (shift_id, volunteer_id). One query."""
        if not shift_ids or not volunteer_ids:
            return {}
        rows = await self.fetch_all_mappings(
            select(shift_wishes.c.shift_id, shift_wishes.c.volunteer_id, shift_wishes.c.answer).where(
                shift_wishes.c.shift_id.in_(shift_ids),
                shift_wishes.c.volunteer_id.in_(volunteer_ids),
            )
        )
        return {(row["shift_id"], row["volunteer_id"]): row["answer"] for row in rows}

    async def list_rules_for(
        self, semesters: list[int], volunteer_ids: list[int]
    ) -> dict[tuple[int, int, int], str]:
        """Weekday answers, keyed (volunteer_id, semester, weekday). One query."""
        if not semesters or not volunteer_ids:
            return {}
        rows = await self.fetch_all_mappings(
            select(
                shift_availability_rules.c.volunteer_id,
                shift_availability_rules.c.semester,
                shift_availability_rules.c.weekday,
                shift_availability_rules.c.answer,
            ).where(
                shift_availability_rules.c.semester.in_(semesters),
                shift_availability_rules.c.volunteer_id.in_(volunteer_ids),
            )
        )
        return {
            (row["volunteer_id"], row["semester"], row["weekday"]): row["answer"] for row in rows
        }

    async def count_shifts_by_volunteer(
        self, starts_from: datetime, starts_before: datetime
    ) -> dict[int, int]:
        rows = await self.fetch_all_mappings(
            select(
                shift_assignments.c.volunteer_id,
                func.count(func.distinct(shift_slots.c.shift_id)).label("shift_count"),
            )
            .select_from(
                shift_assignments.join(
                    shift_slots, shift_slots.c.id == shift_assignments.c.slot_id
                ).join(shifts, shifts.c.id == shift_slots.c.shift_id)
            )
            .where(shifts.c.starts_at >= starts_from, shifts.c.starts_at < starts_before)
            .group_by(shift_assignments.c.volunteer_id)
        )
        return {row["volunteer_id"]: row["shift_count"] for row in rows}

    async def get_volunteer_name(self, volunteer_id: int) -> str | None:
        row = await self.fetch_first_mapping(
            select(volunteer_records.c.first_name, volunteer_records.c.last_name).where(
                volunteer_records.c.id == volunteer_id
            )
        )
        return build_full_name(row["first_name"], row["last_name"]) if row else None

    async def get_volunteer_birth_date(self, volunteer_id: int) -> date | None:
        return await self.fetch_scalar(
            select(volunteer_records.c.birth_date).where(volunteer_records.c.id == volunteer_id)
        )

    async def get_birth_dates(self, volunteer_ids: list[int]) -> dict[int, date | None]:
        if not volunteer_ids:
            return {}
        rows = await self.fetch_all_mappings(
            select(volunteer_records.c.id, volunteer_records.c.birth_date).where(
                volunteer_records.c.id.in_(volunteer_ids)
            )
        )
        return {row["id"]: row["birth_date"] for row in rows}

    async def add_assignment(self, slot_id: int, volunteer_id: int) -> int:
        return await self.fetch_scalar(
            insert(shift_assignments)
            .values(
                slot_id=slot_id,
                volunteer_id=volunteer_id,
                swap_requested=False,
                cannot_attend=False,
            )
            .returning(shift_assignments.c.id)
        )

    async def move_assignment(self, assignment_id: int, slot_id: int) -> None:
        await self.execute(
            update(shift_assignments)
            .where(shift_assignments.c.id == assignment_id)
            .values(
                slot_id=slot_id,
                swap_requested=False,
                cannot_attend=False,
                status_message=None,
                status_changed_at=None,
            )
        )

    async def lock_shift(self, shift_id: int) -> None:
        """Hold the shift row until commit so two changes cannot fill one place."""
        await self.fetch_scalar(select(shifts.c.id).where(shifts.c.id == shift_id).with_for_update())

    async def set_shift_closed(self, shift_id: int, closed: bool) -> None:
        await self.execute(update(shifts).where(shifts.c.id == shift_id).values(is_closed=closed))

    async def delete_shift(self, shift_id: int) -> None:
        """Delete a shift with its places and wishes. The caller makes sure nobody is placed."""
        slot_ids = select(shift_slots.c.id).where(shift_slots.c.shift_id == shift_id)
        await self.execute(delete(shift_assignments).where(shift_assignments.c.slot_id.in_(slot_ids)))
        await self.execute(delete(shift_wishes).where(shift_wishes.c.shift_id == shift_id))
        await self.execute(delete(shift_slots).where(shift_slots.c.shift_id == shift_id))
        await self.execute(delete(shifts).where(shifts.c.id == shift_id))

    async def remove_assignment(self, assignment_id: int) -> None:
        await self.execute(delete(shift_assignments).where(shift_assignments.c.id == assignment_id))

    # ------------------------------------------------------------ closed periods

    async def list_closed_periods(self, group_ids: list[int] | None = None) -> list[ClosedPeriod]:
        stmt = _closed_periods_select().order_by(
            shift_closed_periods.c.starts_on.asc(), shift_closed_periods.c.id.asc()
        )
        if group_ids is not None:
            if not group_ids:
                return []
            stmt = stmt.where(shift_closed_periods.c.group_id.in_(group_ids))
        return [_closed_period(row) for row in await self.fetch_all_mappings(stmt)]

    async def get_closed_period(self, period_id: int) -> ClosedPeriod | None:
        row = await self.fetch_first_mapping(
            _closed_periods_select().where(shift_closed_periods.c.id == period_id)
        )
        return _closed_period(row) if row else None

    async def add_closed_period(
        self, group_id: int, starts_on: date, ends_on: date, label: str
    ) -> None:
        await self.session.execute(
            insert(shift_closed_periods).values(
                group_id=group_id, starts_on=starts_on, ends_on=ends_on, label=label
            )
        )

    async def delete_closed_period(self, period_id: int) -> None:
        await self.session.execute(
            delete(shift_closed_periods).where(shift_closed_periods.c.id == period_id)
        )

    # ----------------------------------------------------------------- templates

    async def list_templates(self, group_ids: list[int]) -> list[ShiftTemplate]:
        if not group_ids:
            return []
        return await self._load_templates(shift_templates.c.group_id.in_(group_ids))

    async def get_template(self, template_id: int) -> ShiftTemplate | None:
        found = await self._load_templates(shift_templates.c.id == template_id)
        return found[0] if found else None

    async def _load_templates(self, condition) -> list[ShiftTemplate]:
        rows = await self.fetch_all_mappings(
            select(shift_templates, groups.c.name.label("group_name"))
            .select_from(shift_templates.join(groups, groups.c.id == shift_templates.c.group_id))
            .where(condition)
            .order_by(
                groups.c.name.asc(),
                shift_templates.c.weekday.asc(),
                shift_templates.c.start_time.asc(),
                shift_templates.c.id.asc(),
            )
        )
        templates = {
            row["id"]: ShiftTemplate(
                template_id=row["id"],
                group_id=row["group_id"],
                group_name=row["group_name"],
                venue=row["venue"],
                title=row["title"],
                weekday=row["weekday"],
                start_time=_as_time(row["start_time"]),
                end_time=_as_time(row["end_time"]),
                training_replaces_slot=row["training_replaces_slot"],
                is_active=row["is_active"],
            )
            for row in rows
        }
        if templates:
            slot_rows = await self.fetch_all_mappings(
                select(shift_template_slots)
                .where(shift_template_slots.c.template_id.in_(list(templates)))
                .order_by(shift_template_slots.c.sort_order.asc(), shift_template_slots.c.id.asc())
            )
            for row in slot_rows:
                templates[row["template_id"]].slots.append(
                    TemplateSlot(row["kind"], row["count"], row["open_for_samf"], row["sort_order"])
                )
        return list(templates.values())

    async def create_template(self, group_id: int, data) -> int:
        template_id = await self.fetch_scalar(
            insert(shift_templates)
            .values(
                group_id=group_id,
                venue=data.venue,
                title=data.title,
                weekday=data.weekday,
                start_time=data.start_time,
                end_time=data.end_time,
                training_replaces_slot=data.training_replaces_slot,
                is_active=True,
            )
            .returning(shift_templates.c.id)
        )
        await self._insert_template_slots(template_id, data.slots)
        return template_id

    async def update_template(self, template_id: int, data) -> None:
        await self.session.execute(
            update(shift_templates)
            .where(shift_templates.c.id == template_id)
            .values(
                venue=data.venue,
                title=data.title,
                weekday=data.weekday,
                start_time=data.start_time,
                end_time=data.end_time,
                training_replaces_slot=data.training_replaces_slot,
            )
        )
        await self.session.execute(
            delete(shift_template_slots).where(shift_template_slots.c.template_id == template_id)
        )
        await self._insert_template_slots(template_id, data.slots)

    async def _insert_template_slots(self, template_id: int, slots: list[TemplateSlot]) -> None:
        if not slots:
            return
        await self.session.execute(
            insert(shift_template_slots),
            [
                {
                    "template_id": template_id,
                    "kind": slot.kind,
                    "count": slot.count,
                    "open_for_samf": slot.open_for_samf,
                    "sort_order": slot.sort_order,
                }
                for slot in slots
            ],
        )

    async def set_template_active(self, template_id: int, active: bool) -> None:
        await self.session.execute(
            update(shift_templates).where(shift_templates.c.id == template_id).values(is_active=active)
        )

    # ----------------------------------------------------------------- generator

    async def list_existing_shift_days(
        self, template_ids: list[int], starts_from: datetime, starts_before: datetime
    ) -> set[tuple[int, date]]:
        """(template id, Oslo date of the start) of shifts already made from the templates."""
        if not template_ids:
            return set()
        rows = await self.fetch_all_mappings(
            select(shifts.c.template_id, shifts.c.starts_at).where(
                shifts.c.template_id.in_(template_ids),
                shifts.c.starts_at >= starts_from,
                shifts.c.starts_at < starts_before,
            )
        )
        return {(row["template_id"], _aware(row["starts_at"]).astimezone(OSLO).date()) for row in rows}

    async def create_planned_shift(self, planned: PlannedShift) -> None:
        shift_id = await self.fetch_scalar(
            insert(shifts)
            .values(
                group_id=planned.group_id,
                template_id=planned.template_id,
                venue=planned.venue,
                title=planned.title,
                starts_at=planned.starts_at,
                ends_at=planned.ends_at,
                note=None,
                is_closed=False,
                training_replaces_slot=planned.training_replaces_slot,
            )
            .returning(shifts.c.id)
        )
        if not planned.slots:
            return
        await self.session.execute(
            insert(shift_slots),
            [
                {
                    "shift_id": shift_id,
                    "kind": slot.kind,
                    "count": slot.count,
                    "open_for_samf": slot.open_for_samf,
                    "sort_order": slot.sort_order,
                }
                for slot in planned.slots
            ],
        )


def _shift_select():
    return select(
        shifts.c.id,
        shifts.c.group_id,
        groups.c.name.label("group_name"),
        shifts.c.venue,
        shifts.c.title,
        shifts.c.starts_at,
        shifts.c.ends_at,
        shifts.c.note,
        shifts.c.is_closed,
        shifts.c.training_replaces_slot,
    ).select_from(shifts.join(groups, groups.c.id == shifts.c.group_id))



def _aware(value: datetime | str | None) -> datetime:
    moment = require_datetime(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _as_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def _closed_periods_select():
    return select(shift_closed_periods, groups.c.name.label("group_name")).select_from(
        shift_closed_periods.join(groups, groups.c.id == shift_closed_periods.c.group_id)
    )


def _closed_period(row) -> ClosedPeriod:
    return ClosedPeriod(
        period_id=row["id"],
        group_id=row["group_id"],
        starts_on=_as_date(row["starts_on"]),
        ends_on=_as_date(row["ends_on"]),
        label=row["label"],
        group_name=row["group_name"],
    )


def _as_time(value) -> time:
    return time.fromisoformat(value) if isinstance(value, str) else value
