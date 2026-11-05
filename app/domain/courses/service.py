from __future__ import annotations

import logging
from time import perf_counter
from typing import Protocol

from app.observability import log_operation_timing
from app.shared.semester import format_semester_code

from app.domain.courses.models import (
    CourseCompletionItem,
    CourseDetail,
    CourseListItem,
    RequiredGroupItem,
)
from app.domain.courses.errors import (
    CourseCompletionNotFoundError,
    CourseDeleteBlockedError,
    DuplicateCourseCompletionError,
    InvalidCourseCompletionError,
)
from app.domain.courses.repository import CoursesRepository

# Re-export for backward compatibility with existing imports
__all__ = [
    "CourseCompletionItem",
    "CourseCompletionNotFoundError",
    "CourseDeleteBlockedError",
    "CourseDetail",
    "CourseListItem",
    "CoursesService",
    "CoursesServiceProtocol",
    "DuplicateCourseCompletionError",
    "InvalidCourseCompletionError",
    "RequiredGroupItem",
]

logger = logging.getLogger("app.performance")


class CoursesServiceProtocol(Protocol):
    async def list_courses(
        self, query: str | None = None, limit: int = 100
    ) -> list[CourseListItem]: ...
    async def get_course_detail(self, course_id: int) -> CourseDetail | None: ...
    async def create_course(self, *, name: str, description: str | None) -> int: ...
    async def update_course(
        self, course_id: int, *, name: str, description: str | None
    ) -> bool: ...
    async def delete_course(self, course_id: int) -> bool: ...
    async def get_training_qualification(self, course_id: int) -> str | None: ...
    async def set_training_qualification(
        self, course_id: int, qualification: str | None, *, allowed: set[str]
    ) -> None: ...
    async def create_course_completion(
        self, *, course_id: int, volunteer_id: int, year: int, term: int
    ) -> int: ...
    async def create_course_completions(
        self,
        *,
        course_id: int,
        volunteer_ids: list[int],
        year: int,
        term: int,
    ) -> int: ...
    async def delete_course_completion(
        self, *, course_id: int, completion_id: int
    ) -> None: ...


class CoursesService:
    def __init__(self, repository: CoursesRepository) -> None:
        self.repository = repository

    async def list_courses(
        self, query: str | None = None, limit: int = 100
    ) -> list[CourseListItem]:
        return await self.repository.list_courses(query=query, limit=limit)

    async def get_course_detail(self, course_id: int) -> CourseDetail | None:
        started_at = perf_counter()
        try:
            return await self.repository.get_course_detail(course_id)
        finally:
            log_operation_timing(
                logger,
                operation="courses.detail",
                started_at=started_at,
                details={"course_id": course_id},
            )

    async def create_course(self, *, name: str, description: str | None) -> int:
        return await self.repository.create_course(name=name, description=description)

    async def update_course(
        self, course_id: int, *, name: str, description: str | None
    ) -> bool:
        return await self.repository.update_course(
            course_id, name=name, description=description
        )

    async def get_training_qualification(self, course_id: int) -> str | None:
        return await self.repository.get_training_qualification(course_id)

    async def set_training_qualification(
        self, course_id: int, qualification: str | None, *, allowed: set[str]
    ) -> None:
        """Say which qualification's approval registers this course (None: none). ``allowed`` are
        the keys of the qualification types that exist. One course per qualification: another
        course that already has it must be cleared first."""
        if qualification is not None:
            if qualification not in allowed:
                raise InvalidCourseCompletionError("Ukjent kvalifikasjon.")
            taken = await self.repository.course_with_training_qualification(qualification)
            if taken is not None and taken[0] != course_id:
                raise InvalidCourseCompletionError(
                    f"Kvalifikasjonen brukes allerede av kurset «{taken[1]}». Fjern den der først."
                )
        await self.repository.set_training_qualification(course_id, qualification)

    async def delete_course(self, course_id: int) -> bool:
        blockers = await self.repository.get_course_delete_blockers(course_id)
        if blockers:
            raise CourseDeleteBlockedError(blockers)
        return await self.repository.delete_course(course_id)

    async def create_course_completion(
        self, *, course_id: int, volunteer_id: int, year: int, term: int
    ) -> int:
        semester_code = _build_semester_code(year=year, term=term)
        if not await self.repository.course_exists(course_id):
            raise InvalidCourseCompletionError("Selected course was not found.")
        if not await self.repository.volunteer_exists(volunteer_id):
            raise InvalidCourseCompletionError("Selected volunteer was not found.")
        if await self.repository.course_completion_exists(
            course_id=course_id,
            volunteer_id=volunteer_id,
            semester_code=semester_code,
        ):
            raise DuplicateCourseCompletionError(
                "Dette kurset er allerede registrert for valgt semester."
            )
        return await self.repository.create_course_completion(
            course_id=course_id,
            volunteer_id=volunteer_id,
            semester_code=semester_code,
        )

    async def create_course_completions(
        self,
        *,
        course_id: int,
        volunteer_ids: list[int],
        year: int,
        term: int,
    ) -> int:
        semester_code = _build_semester_code(year=year, term=term)
        normalized_volunteer_ids = [int(vid) for vid in volunteer_ids]
        if not normalized_volunteer_ids:
            raise InvalidCourseCompletionError("Velg minst én frivillig.")
        if len(set(normalized_volunteer_ids)) != len(normalized_volunteer_ids):
            raise InvalidCourseCompletionError(
                "Den samme frivillige kan ikke velges flere ganger."
            )
        if not await self.repository.course_exists(course_id):
            raise InvalidCourseCompletionError("Selected course was not found.")
        existing_ids = await self.repository.list_existing_volunteer_ids(
            normalized_volunteer_ids
        )
        missing = [
            vid for vid in normalized_volunteer_ids if vid not in existing_ids
        ]
        if missing:
            raise InvalidCourseCompletionError("Selected volunteer was not found.")
        dupes = await self.repository.list_existing_course_completion_volunteer_ids(
            course_id=course_id,
            volunteer_ids=normalized_volunteer_ids,
            semester_code=semester_code,
        )
        if dupes:
            raise DuplicateCourseCompletionError(
                "Dette kurset er allerede registrert for valgt semester."
            )
        return await self.repository.create_course_completions_bulk(
            course_id=course_id,
            volunteer_ids=normalized_volunteer_ids,
            semester_code=semester_code,
        )

    async def delete_course_completion(
        self, *, course_id: int, completion_id: int
    ) -> None:
        row = await self.repository.find_completion(completion_id=completion_id)
        if not row or row["id_kurs"] != course_id:
            raise CourseCompletionNotFoundError(
                f"Course completion {completion_id} was not found."
            )
        await self.repository.delete_course_completion(completion_id)


def _build_semester_code(*, year: int, term: int) -> int:
    if year < 1900 or year > 3000:
        raise InvalidCourseCompletionError("Year must be between 1900 and 3000.")
    if term not in {1, 2}:
        raise InvalidCourseCompletionError("Semester must be Vår or Høst.")
    semester_code = year * 10 + term
    if not format_semester_code(semester_code):
        raise InvalidCourseCompletionError("Unsupported semester code.")
    return semester_code
