from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import delete, func, insert, select, update

from app.db.repository import SqlAlchemyRepository
from app.domain.admin_accounts.tables import group_admin_memberships
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.planning import OSLO
from app.domain.shifts.models import (
    AssignedPerson,
    AssignmentRef,
    MemberRole,
    ShiftGroupOption,
    ShiftRecord,
    SlotRecord,
)
from app.domain.shifts.tables import (
    shift_assignments,
    shift_group_settings,
    shift_slots,
    shift_templates,
    shift_week_status,
    shifts,
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

    async def list_skjenke_root_ids(self, slugs: frozenset[str]) -> list[int]:
        return await self.fetch_scalars_all(
            select(groups.c.id).where(groups.c.slug.in_(sorted(slugs)), groups.c.is_active.is_(True))
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
            )
            .select_from(
                groups.outerjoin(shift_group_settings, shift_group_settings.c.group_id == groups.c.id)
            )
            .where(groups.c.is_active.is_(True))
            .order_by(groups.c.name.asc())
        )

    async def save_group_settings(
        self, group_id: int, *, app_enabled: bool, samf_enabled: bool, meeting_point: str | None
    ) -> None:
        values = {
            "app_enabled": app_enabled,
            "samf_enabled": samf_enabled,
            "meeting_point": meeting_point,
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

    async def remove_assignment(self, assignment_id: int) -> None:
        await self.execute(delete(shift_assignments).where(shift_assignments.c.id == assignment_id))


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
