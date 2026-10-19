"""Data access for the admin agent. Reads are bounded; writes are narrow."""

from __future__ import annotations

from datetime import date
from typing import Any

import re

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import DBAPIError

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


SELECT_MAX_ROWS = 100
_FORBIDDEN_SQL = re.compile(r"\b(set_config|pg_\w+|dblink\w*|lo_\w+|copy)\b", re.IGNORECASE)


class _Done(Exception):
    """Raised to roll back the savepoint that scoped role and settings."""


class SelectRejectedError(Exception):
    pass


def check_select(sql: str) -> str:
    sql = sql.strip().rstrip(";").strip()
    if not re.match(r"(select|with)\b", sql, re.IGNORECASE) or ";" in sql:
        raise SelectRejectedError("Only a single SELECT/WITH statement is allowed.")
    if _FORBIDDEN_SQL.search(sql):
        raise SelectRejectedError("That function or keyword is not allowed.")
    return sql


class AgentSelectMixin(SqlAlchemyRepository):
    """Model-written SELECT, confined by Postgres itself.

    Runs as the NOLOGIN ``agent_reader`` role, which can only SELECT the
    secret-free views in schema ``agent``. The role, read-only mode, timeout and
    search path are SET LOCAL inside a savepoint that is always rolled back, so
    nothing leaks into the rest of the request's transaction.
    """

    async def run_select(self, sql: str) -> dict[str, Any]:
        sql = check_select(sql)
        rows: list[dict[str, Any]] = []
        error: str | None = None
        try:
            async with self.session.begin_nested():
                await self.execute(text("SET LOCAL transaction_read_only = on"))
                await self.execute(text("SET LOCAL ROLE agent_reader"))
                await self.execute(text("SET LOCAL statement_timeout = '5s'"))
                await self.execute(text("SET LOCAL search_path = agent"))
                result = await self.session.execute(text(sql))
                rows = [dict(r) for r in result.mappings().fetchmany(SELECT_MAX_ROWS + 1)]
                raise _Done
        except _Done:
            pass
        except DBAPIError as exc:
            error = str(exc.orig).split(": ", 1)[-1].splitlines()[0][:300]
        if error:
            return {"error": error}
        return {"rows": rows[:SELECT_MAX_ROWS], "truncated": len(rows) > SELECT_MAX_ROWS}


class AgentChatRepository(AgentSelectMixin):
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
