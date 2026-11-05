from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Integer,
    Text,
    delete,
    func,
    insert,
    literal,
    or_,
    select,
    union_all,
    update,
)

from app.db.repository import SqlAlchemyRepository
from app.domain.courses.tables import course_completions, courses, group_course_requirements
from app.domain.groups.tables import groups
from app.domain.volunteers.tables import volunteer_records
from app.shared.semester import format_semester_code
from app.shared.coercion import coerce_datetime
from app.shared.text import build_full_name

from app.domain.courses.models import (
    CourseCompletionItem,
    CourseDetail,
    CourseListItem,
    RequiredGroupItem,
)

logger = logging.getLogger("app.performance")


class CoursesRepository(SqlAlchemyRepository):
    async def list_courses(
        self, query: str | None = None, limit: int = 100
    ) -> list[CourseListItem]:
        stmt = (
            select(
                courses.c.id,
                courses.c.name,
                courses.c.description,
                courses.c.created_at,
            )
            .order_by(courses.c.name.asc(), courses.c.id.asc())
            .limit(limit)
        )
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(courses.c.name.ilike(pattern), courses.c.description.ilike(pattern))
            )
        rows = await self.fetch_all_mappings(stmt)
        return [
            CourseListItem(
                course_id=row["id"],
                name=row["name"],
                description=row["description"],
                created_at=coerce_datetime(row["created_at"]),
            )
            for row in rows
        ]

    async def get_course_detail(self, course_id: int) -> CourseDetail | None:
        course_select = select(
            literal("course").label("row_type"),
            courses.c.id.label("course_id"),
            courses.c.name.label("course_name"),
            courses.c.description.label("course_description"),
            courses.c.created_at.label("course_created_at"),
            literal(None, type_=BigInteger()).label("group_id"),
            literal(None, type_=Text()).label("group_name"),
            literal(None, type_=BigInteger()).label("completion_id"),
            literal(None, type_=BigInteger()).label("volunteer_id"),
            literal(None, type_=Text()).label("volunteer_first_name"),
            literal(None, type_=Text()).label("volunteer_last_name"),
            literal(None, type_=Integer()).label("completed_semester"),
        ).where(courses.c.id == course_id)
        groups_select = (
            select(
                literal("group").label("row_type"),
                literal(None, type_=BigInteger()).label("course_id"),
                literal(None, type_=Text()).label("course_name"),
                literal(None, type_=Text()).label("course_description"),
                literal(None, type_=courses.c.created_at.type).label(
                    "course_created_at"
                ),
                groups.c.id.label("group_id"),
                groups.c.name.label("group_name"),
                literal(None, type_=BigInteger()).label("completion_id"),
                literal(None, type_=BigInteger()).label("volunteer_id"),
                literal(None, type_=Text()).label("volunteer_first_name"),
                literal(None, type_=Text()).label("volunteer_last_name"),
                literal(None, type_=Integer()).label("completed_semester"),
            )
            .select_from(
                group_course_requirements.join(
                    groups, groups.c.id == group_course_requirements.c.group_id
                )
            )
            .where(group_course_requirements.c.course_id == course_id)
        )
        recent_completions = (
            select(
                course_completions.c.id,
                course_completions.c.completed_semester,
                volunteer_records.c.id.label("volunteer_id"),
                volunteer_records.c.first_name,
                volunteer_records.c.last_name,
            )
            .select_from(
                course_completions.join(
                    volunteer_records,
                    volunteer_records.c.id == course_completions.c.volunteer_id,
                )
            )
            .where(course_completions.c.course_id == course_id)
            .order_by(
                course_completions.c.completed_semester.desc(),
                course_completions.c.id.desc(),
            )
            .limit(20)
            .subquery()
        )
        completions_select = select(
            literal("completion").label("row_type"),
            literal(None, type_=BigInteger()).label("course_id"),
            literal(None, type_=Text()).label("course_name"),
            literal(None, type_=Text()).label("course_description"),
            literal(None, type_=courses.c.created_at.type).label("course_created_at"),
            literal(None, type_=BigInteger()).label("group_id"),
            literal(None, type_=Text()).label("group_name"),
            recent_completions.c.id.label("completion_id"),
            recent_completions.c.volunteer_id.label("volunteer_id"),
            recent_completions.c.first_name.label("volunteer_first_name"),
            recent_completions.c.last_name.label("volunteer_last_name"),
            recent_completions.c.completed_semester.label("completed_semester"),
        )
        rows = await self.fetch_all_mappings(
            union_all(course_select, groups_select, completions_select)
        )
        course_row = next((row for row in rows if row["row_type"] == "course"), None)
        if course_row is None:
            return None
        required_groups: list[RequiredGroupItem] = []
        seen_group_ids: set[int] = set()
        recent_completion_items: list[CourseCompletionItem] = []
        seen_completion_ids: set[int] = set()
        for row in rows:
            if (
                row["row_type"] == "group"
                and row["group_id"] is not None
                and row["group_id"] not in seen_group_ids
            ):
                seen_group_ids.add(row["group_id"])
                required_groups.append(
                    RequiredGroupItem(
                        group_id=row["group_id"], group_name=row["group_name"]
                    )
                )
            if (
                row["row_type"] == "completion"
                and row["completion_id"] is not None
                and row["completion_id"] not in seen_completion_ids
            ):
                seen_completion_ids.add(row["completion_id"])
                recent_completion_items.append(
                    CourseCompletionItem(
                        completion_id=row["completion_id"],
                        volunteer_id=row["volunteer_id"],
                        volunteer_name=build_full_name(
                            row.get("volunteer_first_name"),
                            row.get("volunteer_last_name"),
                        ),
                        completed_semester_code=row["completed_semester"],
                        completed_semester_label=format_semester_code(
                            row["completed_semester"]
                        )
                        or str(row["completed_semester"]),
                    )
                )
        return CourseDetail(
            course_id=course_row["course_id"],
            name=course_row["course_name"],
            description=course_row["course_description"],
            created_at=coerce_datetime(course_row.get("course_created_at")),
            required_groups=sorted(
                required_groups,
                key=lambda item: (item.group_name.lower(), item.group_id),
            ),
            recent_completions=sorted(
                recent_completion_items,
                key=lambda item: (item.completed_semester_code, item.completion_id),
                reverse=True,
            ),
            delete_blockers=await self._get_course_delete_blockers(course_id),
        )

    async def create_course(self, *, name: str, description: str | None) -> int:
        row = await self.execute_one_mapping(
            insert(courses)
            .values(
                name=name.strip(),
                description=(
                    description.strip() if description and description.strip() else None
                ),
                created_at=datetime.now(UTC),
            )
            .returning(courses.c.id)
        )
        return row["id"]

    async def update_course(
        self, course_id: int, *, name: str, description: str | None
    ) -> bool:
        async def callback(session):
            result = await session.execute(
                update(courses)
                .where(courses.c.id == course_id)
                .values(
                    name=name.strip(),
                    description=(
                        description.strip()
                        if description and description.strip()
                        else None
                    ),
                )
                .returning(courses.c.id)
            )
            row = result.first()
            return row[0] if row is not None else None

        updated_course_id = await callback(self.session)
        return updated_course_id == course_id

    async def get_training_qualification(self, course_id: int) -> str | None:
        """The qualification this course is registered by (skjenker, kokk, skiftleder), or None."""
        row = await self.fetch_first_mapping(
            select(courses.c.training_qualification).where(courses.c.id == course_id)
        )
        return row["training_qualification"] if row else None

    async def course_with_training_qualification(self, qualification: str) -> tuple[int, str] | None:
        row = await self.fetch_first_mapping(
            select(courses.c.id, courses.c.name).where(courses.c.training_qualification == qualification)
        )
        return (row["id"], row["name"]) if row else None

    async def set_training_qualification(self, course_id: int, qualification: str | None) -> None:
        await self.execute(
            update(courses).where(courses.c.id == course_id).values(training_qualification=qualification)
        )

    async def delete_course(self, course_id: int) -> bool:
        async def callback(session):
            await session.execute(
                delete(group_course_requirements).where(
                    group_course_requirements.c.course_id == course_id
                )
            )
            result = await session.execute(
                delete(courses)
                .where(courses.c.id == course_id)
                .returning(courses.c.id)
            )
            row = result.first()
            return row[0] if row is not None else None

        deleted_course_id = await callback(self.session)
        return deleted_course_id == course_id

    async def create_course_completion(
        self, *, course_id: int, volunteer_id: int, semester_code: int
    ) -> int:
        row = await self.execute_one_mapping(
            insert(course_completions)
            .values(
                volunteer_id=volunteer_id,
                course_id=course_id,
                completed_semester=semester_code,
            )
            .returning(course_completions.c.id)
        )
        return int(row["id"])

    async def create_course_completions_bulk(
        self, *, course_id: int, volunteer_ids: list[int], semester_code: int
    ) -> int:
        async def callback(session):
            result = await session.execute(
                insert(course_completions).returning(course_completions.c.id),
                [
                    {
                        "volunteer_id": volunteer_id,
                        "course_id": course_id,
                        "completed_semester": semester_code,
                    }
                    for volunteer_id in volunteer_ids
                ],
            )
            return result.scalars().all()

        created_ids = await callback(self.session)
        return len(created_ids)

    async def delete_course_completion(self, completion_id: int) -> None:
        await self.execute(
            delete(course_completions).where(
                course_completions.c.id == completion_id
            )
        )

    async def find_completion(self, *, completion_id: int) -> dict | None:
        return await self.fetch_first_mapping(
            select(course_completions.c.id, course_completions.c.course_id)
            .where(course_completions.c.id == completion_id)
            .limit(1)
        )

    async def course_exists(self, course_id: int) -> bool:
        return bool(
            await self.fetch_scalar(
                select(func.count())
                .select_from(courses)
                .where(courses.c.id == course_id)
            )
        )

    async def volunteer_exists(self, volunteer_id: int) -> bool:
        return bool(
            await self.fetch_scalar(
                select(func.count())
                .select_from(volunteer_records)
                .where(volunteer_records.c.id == volunteer_id)
            )
        )

    async def list_existing_volunteer_ids(
        self, volunteer_ids: list[int]
    ) -> set[int]:
        rows = await self.fetch_all_mappings(
            select(volunteer_records.c.id).where(
                volunteer_records.c.id.in_(volunteer_ids)
            )
        )
        return {int(row["id"]) for row in rows}

    async def course_completion_exists(
        self, *, course_id: int, volunteer_id: int, semester_code: int
    ) -> bool:
        stmt = (
            select(func.count())
            .select_from(course_completions)
            .where(course_completions.c.course_id == course_id)
            .where(course_completions.c.volunteer_id == volunteer_id)
            .where(course_completions.c.completed_semester == semester_code)
        )
        return bool(await self.fetch_scalar(stmt))

    async def list_existing_course_completion_volunteer_ids(
        self, *, course_id: int, volunteer_ids: list[int], semester_code: int
    ) -> set[int]:
        rows = await self.fetch_all_mappings(
            select(course_completions.c.volunteer_id)
            .where(course_completions.c.course_id == course_id)
            .where(course_completions.c.volunteer_id.in_(volunteer_ids))
            .where(course_completions.c.completed_semester == semester_code)
        )
        return {int(row["volunteer_id"]) for row in rows}

    async def _get_course_delete_blockers(self, course_id: int) -> list[str]:
        completion_count = await self.fetch_scalar(
            select(func.count())
            .select_from(course_completions)
            .where(course_completions.c.course_id == course_id)
        )
        blockers: list[str] = []
        if completion_count:
            blockers.append("Kurset har fullføringer og kan ikke slettes.")
        return blockers
