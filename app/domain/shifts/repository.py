from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.repository import SqlAlchemyRepository
from app.domain.groups.tables import groups
from app.domain.shifts.models import (
    AssignedPerson,
    ShiftGroupOption,
    ShiftRecord,
    SlotRecord,
)
from app.domain.shifts.tables import shift_assignments, shift_slots, shift_templates, shifts
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

    async def list_shifts_between(
        self,
        starts_from: datetime,
        starts_before: datetime,
        group_id: int | None = None,
    ) -> list[ShiftRecord]:
        shift_stmt = (
            select(
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
            )
            .select_from(shifts.join(groups, groups.c.id == shifts.c.group_id))
            .where(shifts.c.starts_at >= starts_from, shifts.c.starts_at < starts_before)
            .order_by(shifts.c.starts_at.asc(), shifts.c.id.asc())
        )
        if group_id is not None:
            shift_stmt = shift_stmt.where(shifts.c.group_id == group_id)
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
                    )
                )
        return list(records.values())


def _aware(value: datetime | str | None) -> datetime:
    moment = require_datetime(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
