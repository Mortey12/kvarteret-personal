"""Data access for the admin agent. Reads are bounded; writes are narrow."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, or_, select, update

from app.db.repository import SqlAlchemyRepository
from app.domain.courses.tables import course_completions, courses
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.volunteers.tables import (
    volunteer_cards,
    volunteer_next_of_kin,
    volunteer_records,
)

MAX_ROWS = 25

# Column names the agent may change on a volunteer -> table column.
EDITABLE_PROFILE_FIELDS = {
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "address": "street_address",
    "postal_code": "postal_code",
    "birth_date": "birth_date",
}
_PROFILE_COLUMNS = (
    volunteer_records.c.id,
    volunteer_records.c.first_name,
    volunteer_records.c.last_name,
    volunteer_records.c.email,
    volunteer_records.c.phone,
    volunteer_records.c.birth_date,
    volunteer_records.c.street_address.label("address"),
    volunteer_records.c.postal_code,
)


class AgentChatRepository(SqlAlchemyRepository):
    async def list_groups(self, query: str | None) -> list[dict[str, Any]]:
        stmt = select(groups.c.id, groups.c.name, groups.c.is_active)
        if query:
            stmt = stmt.where(groups.c.name.ilike(f"%{query}%"))
        return await self.fetch_all_mappings(
            stmt.order_by(groups.c.name).limit(MAX_ROWS)
        )

    async def search_volunteers(
        self, query: str | None, group_id: int | None
    ) -> list[dict[str, Any]]:
        stmt = select(*_PROFILE_COLUMNS)
        if query:
            like = f"%{query}%"
            full_name = func.concat(
                volunteer_records.c.first_name, " ", volunteer_records.c.last_name
            )
            stmt = stmt.where(
                or_(
                    full_name.ilike(like),
                    volunteer_records.c.email.ilike(like),
                    volunteer_records.c.phone.ilike(like),
                )
            )
        if group_id is not None:
            stmt = stmt.where(
                volunteer_records.c.id.in_(
                    select(role_assignments.c.volunteer_id).where(
                        role_assignments.c.group_id == group_id
                    )
                )
            )
        return await self.fetch_all_mappings(
            stmt.order_by(volunteer_records.c.last_name, volunteer_records.c.id).limit(
                MAX_ROWS
            )
        )

    async def get_volunteer(self, volunteer_id: int) -> dict[str, Any] | None:
        profile = await self.fetch_first_mapping(
            select(*_PROFILE_COLUMNS, volunteer_records.c.gender).where(
                volunteer_records.c.id == volunteer_id
            )
        )
        if profile is None:
            return None
        assignments = await self.fetch_all_mappings(
            select(
                role_assignments.c.id,
                role_assignments.c.semester,
                role_assignments.c.contract_signed,
                groups.c.name.label("group"),
                assignment_roles.c.name.label("role"),
            )
            .select_from(
                role_assignments.join(
                    groups, groups.c.id == role_assignments.c.group_id
                ).outerjoin(
                    assignment_roles, assignment_roles.c.id == role_assignments.c.role_id
                )
            )
            .where(role_assignments.c.volunteer_id == volunteer_id)
            .order_by(role_assignments.c.semester.desc())
            .limit(100)
        )
        completions = await self.fetch_all_mappings(
            select(courses.c.name.label("course"), course_completions.c.completed_semester)
            .join(courses, courses.c.id == course_completions.c.course_id)
            .where(course_completions.c.volunteer_id == volunteer_id)
            .limit(100)
        )
        cards = await self.fetch_scalars_all(
            select(volunteer_cards.c.card_number).where(
                volunteer_cards.c.volunteer_id == volunteer_id
            )
        )
        next_of_kin = await self.fetch_all_mappings(
            select(volunteer_next_of_kin.c.name, volunteer_next_of_kin.c.phone).where(
                volunteer_next_of_kin.c.volunteer_id == volunteer_id
            )
        )
        return {
            **profile,
            "assignments": assignments,
            "course_completions": completions,
            "card_numbers": cards,
            "next_of_kin": next_of_kin,
        }

    async def get_assignment(self, assignment_id: int) -> dict[str, Any] | None:
        return await self.fetch_first_mapping(
            select(
                role_assignments.c.id,
                role_assignments.c.volunteer_id,
                role_assignments.c.contract_signed,
            ).where(role_assignments.c.id == assignment_id)
        )

    async def update_volunteer_fields(
        self, volunteer_id: int, values: dict[str, str | date | None]
    ) -> None:
        await self.execute(
            update(volunteer_records)
            .where(volunteer_records.c.id == volunteer_id)
            .values(**{EDITABLE_PROFILE_FIELDS[k]: v for k, v in values.items()})
        )

    async def set_contract_signed(self, assignment_id: int, signed: bool) -> None:
        await self.execute(
            update(role_assignments)
            .where(role_assignments.c.id == assignment_id)
            .values(contract_signed=signed)
        )
