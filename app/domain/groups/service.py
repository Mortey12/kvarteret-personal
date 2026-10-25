from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import (
    case,
    delete,
    func,
    insert,
    select,
    update,
)

from app.domain.admin_accounts.tables import group_admin_memberships
from app.domain.courses.tables import group_course_requirements
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.groups.queries import (
    GroupBreakdownItem,
    GroupDetail,
    GroupListItem,
    GroupMemberCount,
    GroupMemberItem,
    GroupPositionItem,
    GroupsQueries,
    OrgSemesterDetailed,
    SemesterGroup,
    SemesterRetentionStats,
    SemesterStats,
)
from app.shared.semester import get_current_semester_code
from app.shared.slugs import slugify

POSTGRES_INT_MIN = -(2**31)
POSTGRES_INT_MAX = 2**31 - 1


def _validate_role_storage_values(*, group_id: int, pingvin_points: int, role_id: int | None = None) -> None:
    if not POSTGRES_INT_MIN <= pingvin_points <= POSTGRES_INT_MAX:
        raise ValueError("Pingvinpoeng må være mellom -2147483648 og 2147483647.")
    if not 1 <= group_id <= POSTGRES_INT_MAX or (role_id is not None and not 1 <= role_id <= POSTGRES_INT_MAX):
        raise ValueError("Ugyldig gruppe- eller vervnummer.")

__all__ = [
    "GroupBreakdownItem",
    "GroupDeleteBlockedError",
    "GroupDetail",
    "GroupHistoryNotFoundError",
    "GroupListItem",
    "GroupMemberCount",
    "GroupMemberItem",
    "GroupPositionItem",
    "GroupRoleDeleteBlockedError",
    "GroupsService",
    "GroupsServiceProtocol",
    "OrgSemesterDetailed",
    "SemesterGroup",
    "SemesterRetentionStats",
    "SemesterStats",
]


class GroupDeleteBlockedError(ValueError):
    def __init__(self, blockers: list[str]) -> None:
        super().__init__("Group deletion is blocked.")
        self.blockers = blockers


class GroupRoleDeleteBlockedError(ValueError):
    def __init__(self, blockers: list[str]) -> None:
        super().__init__("Group role deletion is blocked.")
        self.blockers = blockers


class GroupHistoryNotFoundError(ValueError):
    pass


class GroupsServiceProtocol(Protocol):
    async def list_groups(self, query: str | None = None, limit: int = 100) -> list[GroupListItem]: ...
    async def get_group_detail(self, group_id: int) -> GroupDetail | None: ...
    async def get_group_history_by_semester(
        self, group_id: int, *, expanded_semesters: int | None = None
    ) -> list[SemesterGroup]: ...
    async def get_group_history_semester(
        self, group_id: int, semester_code: int
    ) -> SemesterGroup: ...
    async def get_group_semester_stats(self, group_id: int) -> list[SemesterStats]: ...
    async def get_current_group_member_counts(self) -> list[GroupMemberCount]: ...
    async def get_org_stats_detailed(self) -> list[OrgSemesterDetailed]: ...
    async def get_org_retention_stats(self) -> list[SemesterRetentionStats]: ...
    async def get_group_retention_stats(self, group_id: int) -> list[SemesterRetentionStats]: ...
    async def create_group(
        self,
        *,
        name: str,
        description: str | None,
        active: bool,
        active_until_semester: int,
        parent_group_id: int | None,
        discount_step: int | None,
    ) -> int: ...
    async def update_group(
        self,
        group_id: int,
        *,
        name: str,
        description: str | None,
        active: bool,
        active_until_semester: int,
        parent_group_id: int | None,
        discount_step: int | None,
        recruitment_contact_email: str | None = None,
    ) -> bool: ...
    async def set_recruitment_contact_volunteer(
        self, group_id: int, volunteer_id: int | None
    ) -> bool: ...
    async def archive_group(self, group_id: int) -> bool: ...
    async def delete_group(self, group_id: int) -> bool: ...
    async def create_group_role(self, group_id: int, *, role_name: str, pingvin_points: int) -> int | None: ...
    async def update_group_role(self, group_id: int, role_id: int, *, role_name: str, pingvin_points: int) -> bool: ...
    async def delete_group_role(self, group_id: int, role_id: int) -> bool: ...
    async def delete_group_history_entry(self, group_id: int, history_id: int) -> None: ...


class GroupsService(GroupsQueries):
    async def create_group(
        self,
        *,
        name: str,
        description: str | None,
        active: bool,
        active_until_semester: int,
        parent_group_id: int | None,
        discount_step: int | None,
    ) -> int:
        normalized_name = name.strip()
        group_slug = slugify(normalized_name)
        if not group_slug:
            raise ValueError("Group name must contain letters or numbers.")
        row = await self.execute_one_mapping(
            insert(groups)
            .values(
                slug=group_slug,
                name=normalized_name,
                description=(description.strip() if description and description.strip() else None),
                is_active=active,
                active_through_semester=active_until_semester,
                parent_group_id=parent_group_id,
                discount_tier=discount_step,
                created_at=datetime.now(UTC),
            )
            .returning(groups.c.id)
        )
        return row["id"]

    async def update_group(
        self,
        group_id: int,
        *,
        name: str,
        description: str | None,
        active: bool,
        active_until_semester: int,
        parent_group_id: int | None,
        discount_step: int | None,
        recruitment_contact_email: str | None = None,
    ) -> bool:
        async def callback(session):
            result = await session.execute(
                update(groups)
                .where(groups.c.id == group_id)
                .values(
                    recruitment_contact_email=(
                        recruitment_contact_email.strip().lower()
                        if recruitment_contact_email and recruitment_contact_email.strip()
                        else None
                    ),
                    name=name.strip(),
                    description=(description.strip() if description and description.strip() else None),
                    is_active=active,
                    active_through_semester=active_until_semester,
                    parent_group_id=parent_group_id,
                    discount_tier=discount_step,
                )
                .returning(groups.c.id)
            )
            row = result.first()
            return row[0] if row is not None else None

        updated_group_id = await callback(self.session)
        return updated_group_id == group_id

    async def set_recruitment_contact_volunteer(
        self, group_id: int, volunteer_id: int | None
    ) -> bool:
        result = await self.session.execute(
            update(groups)
            .where(groups.c.id == group_id)
            .values(recruitment_contact_volunteer_id=volunteer_id)
            .returning(groups.c.id)
        )
        return result.first() is not None

    async def archive_group(self, group_id: int) -> bool:
        current_semester = get_current_semester_code()

        async def callback(session):
            result = await session.execute(
                update(groups)
                .where(groups.c.id == group_id)
                .values(
                    is_active=False,
                    active_through_semester=case(
                        (
                            groups.c.active_through_semester > current_semester,
                            current_semester,
                        ),
                        else_=groups.c.active_through_semester,
                    ),
                )
                .returning(groups.c.id)
            )
            row = result.first()
            return row[0] if row is not None else None

        archived_group_id = await callback(self.session)
        return archived_group_id == group_id

    async def delete_group(self, group_id: int) -> bool:
        blockers = await self._get_group_delete_blockers(group_id)
        if blockers:
            raise GroupDeleteBlockedError(blockers)

        async def callback(session):
            await session.execute(delete(group_admin_memberships).where(group_admin_memberships.c.group_id == group_id))
            await session.execute(
                delete(group_course_requirements).where(group_course_requirements.c.group_id == group_id)
            )
            await session.execute(delete(assignment_roles).where(assignment_roles.c.group_id == group_id))
            result = await session.execute(delete(groups).where(groups.c.id == group_id).returning(groups.c.id))
            row = result.first()
            return row[0] if row is not None else None

        deleted_group_id = await callback(self.session)
        return deleted_group_id == group_id

    async def create_group_role(self, group_id: int, *, role_name: str, pingvin_points: int) -> int | None:
        _validate_role_storage_values(group_id=group_id, pingvin_points=pingvin_points)
        normalized_role_name = role_name.strip()
        if not normalized_role_name:
            raise ValueError("Role name is required.")
        row = await self.execute_one_mapping(
            insert(assignment_roles)
            .values(
                group_id=group_id,
                name=normalized_role_name,
                penguin_points=pingvin_points,
            )
            .returning(assignment_roles.c.id)
        )
        return row["id"] if row is not None else None

    async def update_group_role(self, group_id: int, role_id: int, *, role_name: str, pingvin_points: int) -> bool:
        _validate_role_storage_values(group_id=group_id, role_id=role_id, pingvin_points=pingvin_points)
        normalized_role_name = role_name.strip()
        if not normalized_role_name:
            raise ValueError("Role name is required.")

        async def callback(session):
            result = await session.execute(
                update(assignment_roles)
                .where(
                    assignment_roles.c.id == role_id,
                    assignment_roles.c.group_id == group_id,
                )
                .values(
                    name=normalized_role_name,
                    penguin_points=pingvin_points,
                )
                .returning(assignment_roles.c.id)
            )
            row = result.first()
            return row[0] if row is not None else None

        updated_role_id = await callback(self.session)
        return updated_role_id == role_id

    async def delete_group_role(self, group_id: int, role_id: int) -> bool:
        blockers = await self._get_group_role_delete_blockers(group_id, role_id)
        if blockers:
            raise GroupRoleDeleteBlockedError(blockers)

        async def callback(session):
            result = await session.execute(
                delete(assignment_roles)
                .where(
                    assignment_roles.c.id == role_id,
                    assignment_roles.c.group_id == group_id,
                )
                .returning(assignment_roles.c.id)
            )
            row = result.first()
            return row[0] if row is not None else None

        deleted_role_id = await callback(self.session)
        return deleted_role_id == role_id

    async def delete_group_history_entry(self, group_id: int, history_id: int) -> None:
        row = await self.fetch_one_mapping(
            select(role_assignments.c.id, role_assignments.c.group_id).where(role_assignments.c.id == history_id)
        )
        if row is None or row["group_id"] != group_id:
            raise GroupHistoryNotFoundError(f"Group history entry {history_id} was not found.")
        await self.execute(delete(role_assignments).where(role_assignments.c.id == history_id))

    async def _get_group_role_delete_blockers(self, group_id: int, role_id: int) -> list[str]:
        role_exists = await self.fetch_scalar(
            select(func.count())
            .select_from(assignment_roles)
            .where(
                assignment_roles.c.id == role_id,
                assignment_roles.c.group_id == group_id,
            )
        )
        if not role_exists:
            return []
        history_count = await self.fetch_scalar(
            select(func.count())
            .select_from(role_assignments)
            .where(
                role_assignments.c.role_id == role_id,
                role_assignments.c.group_id == group_id,
            )
        )
        blockers: list[str] = []
        if history_count:
            blockers.append("Vervet har medlemmer og kan ikke slettes.")
        return blockers
