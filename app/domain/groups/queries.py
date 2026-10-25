from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import logging
from time import perf_counter
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Integer,
    Text,
    case,
    distinct,
    func,
    literal,
    or_,
    select,
    union_all,
)

from app.db.repository import SqlAlchemyRepository
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.volunteers.tables import volunteer_photos, volunteer_records
from app.shared.semester import (
    format_semester_code,
    get_current_semester_code,
)
from app.media_tokens import build_photo_media_url
from app.observability import log_operation_timing
from app.shared.coercion import coerce_datetime
from app.shared.text import build_full_name

logger = logging.getLogger("app.performance")


@dataclass(slots=True)
class GroupListItem:
    group_id: int
    name: str
    description: str | None
    active: bool
    active_until_semester: int
    parent_group_id: int | None
    discount_step: int | None


@dataclass(slots=True)
class GroupPositionItem:
    role_id: int
    role_name: str | None
    pingvin_points: int
    assignment_count: int
    delete_blockers: list[str]


@dataclass(slots=True)
class GroupMemberItem:
    history_id: int
    volunteer_id: int
    volunteer_name: str
    photo_url: str | None
    role_name: str | None
    semester_code: int
    semester_label: str
    contract_signed: bool


@dataclass(slots=True)
class SemesterGroup:
    semester_code: int
    semester_label: str
    # None when the semester's members load on demand (history overview).
    members: list[GroupMemberItem] | None
    member_count: int = 0


@dataclass(slots=True)
class SemesterStats:
    semester_code: int
    semester_label: str
    member_count: int


@dataclass(slots=True)
class GroupMemberCount:
    group_id: int
    group_name: str
    member_count: int


@dataclass(slots=True)
class GroupBreakdownItem:
    group_name: str
    member_count: int


@dataclass(slots=True)
class OrgSemesterDetailed:
    semester_code: int
    semester_label: str
    unique_members: int
    groups: list[GroupBreakdownItem]


@dataclass(slots=True)
class SemesterRetentionStats:
    semester_code: int
    semester_label: str
    total_members: int
    retained_from_prev: int
    new_members: int
    retained_to_next_same_group: int
    retained_to_next_other_group: int
    retained_to_next: int
    churned: int


@dataclass(slots=True)
class GroupDetail:
    group_id: int
    name: str
    description: str | None
    active: bool
    active_until_semester: int
    active_until_label: str | None
    parent_group_id: int | None
    discount_step: int | None
    created_at: datetime | None
    recruitment_contact_email: str | None
    positions: list[GroupPositionItem]
    recent_members: list[GroupMemberItem]
    delete_blockers: list[str]
    recruitment_contact_volunteer_id: int | None = None
    recruitment_contact_volunteer_name: str | None = None



class GroupsQueries(SqlAlchemyRepository):
    async def list_groups(
        self, query: str | None = None, limit: int = 100
    ) -> list[GroupListItem]:
        stmt = (
            select(
                groups.c.id,
                groups.c.name,
                groups.c.description,
                groups.c.is_active,
                groups.c.active_through_semester,
                groups.c.parent_group_id,
                groups.c.discount_tier,
            )
            .order_by(groups.c.name.asc(), groups.c.id.asc())
            .limit(limit)
        )
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(groups.c.name.ilike(pattern), groups.c.description.ilike(pattern))
            )
        rows = await self.fetch_all_mappings(stmt)
        return [
            GroupListItem(
                group_id=row["id"],
                name=row["name"],
                description=row["description"],
                active=row["is_active"],
                active_until_semester=row["active_through_semester"],
                parent_group_id=row["parent_group_id"],
                discount_step=row["discount_tier"],
            )
            for row in rows
        ]

    async def get_group_detail(self, group_id: int) -> GroupDetail | None:
        started_at = perf_counter()
        current_semester = get_current_semester_code()
        role_assignment_counts = (
            select(
                role_assignments.c.role_id.label("role_id"),
                func.count().label("assignment_count"),
            )
            .where(role_assignments.c.group_id == group_id, role_assignments.c.role_id.is_not(None))
            .group_by(role_assignments.c.role_id)
            .subquery()
        )
        group_select = select(
            literal("group").label("row_type"),
            groups.c.id.label("group_id"),
            groups.c.name.label("group_name"),
            groups.c.description.label("group_description"),
            groups.c.is_active.label("group_active"),
            groups.c.active_through_semester.label("group_active_until"),
            groups.c.parent_group_id.label("group_parent_id"),
            groups.c.discount_tier.label("group_discount_step"),
            groups.c.created_at.label("group_created_at"),
            literal(None, type_=BigInteger()).label("position_id"),
            literal(None, type_=Text()).label("position_name"),
            literal(None, type_=Integer()).label("position_points"),
            literal(None, type_=Integer()).label("position_assignment_count"),
            literal(None, type_=BigInteger()).label("history_id"),
            literal(None, type_=BigInteger()).label("volunteer_id"),
            literal(None, type_=Text()).label("volunteer_first_name"),
            literal(None, type_=Text()).label("volunteer_last_name"),
            literal(None, type_=Text()).label("volunteer_photo_sha1"),
            literal(None, type_=Text()).label("volunteer_photo_filetype"),
            literal(None, type_=Text()).label("member_role_name"),
            literal(None, type_=Integer()).label("member_semester"),
            literal(None, type_=Boolean()).label("member_contract_signed"),
        ).where(groups.c.id == group_id)
        positions_select = (
            select(
                literal("position").label("row_type"),
                literal(None, type_=BigInteger()).label("group_id"),
                literal(None, type_=Text()).label("group_name"),
                literal(None, type_=Text()).label("group_description"),
                literal(None, type_=Boolean()).label("group_active"),
                literal(None, type_=Integer()).label("group_active_until"),
                literal(None, type_=BigInteger()).label("group_parent_id"),
                literal(None, type_=Integer()).label("group_discount_step"),
                literal(None, type_=groups.c.created_at.type).label("group_created_at"),
                assignment_roles.c.id.label("position_id"),
                assignment_roles.c.name.label("position_name"),
                assignment_roles.c.penguin_points.label("position_points"),
                func.coalesce(role_assignment_counts.c.assignment_count, 0).label(
                    "position_assignment_count"
                ),
                literal(None, type_=BigInteger()).label("history_id"),
                literal(None, type_=BigInteger()).label("volunteer_id"),
                literal(None, type_=Text()).label("volunteer_first_name"),
                literal(None, type_=Text()).label("volunteer_last_name"),
                literal(None, type_=Text()).label("volunteer_photo_sha1"),
                literal(None, type_=Text()).label("volunteer_photo_filetype"),
                literal(None, type_=Text()).label("member_role_name"),
                literal(None, type_=Integer()).label("member_semester"),
                literal(None, type_=Boolean()).label("member_contract_signed"),
            )
            .select_from(
                assignment_roles.outerjoin(
                    role_assignment_counts,
                    role_assignment_counts.c.role_id == assignment_roles.c.id,
                )
            )
            .where(assignment_roles.c.group_id == group_id)
        )
        recent_members = (
            select(
                role_assignments.c.id,
                role_assignments.c.volunteer_id,
                role_assignments.c.semester,
                role_assignments.c.contract_signed,
                volunteer_records.c.first_name,
                volunteer_records.c.last_name,
                volunteer_photos.c.sha1,
                volunteer_photos.c.filetype,
                assignment_roles.c.name.label("role_name"),
            )
            .select_from(
                role_assignments.join(volunteer_records, volunteer_records.c.id == role_assignments.c.volunteer_id)
                .outerjoin(
                    volunteer_photos, volunteer_photos.c.volunteer_id == volunteer_records.c.id
                )
                .outerjoin(assignment_roles, assignment_roles.c.id == role_assignments.c.role_id)
            )
            .where(
                role_assignments.c.group_id == group_id,
                role_assignments.c.semester == current_semester,
            )
            .order_by(
                volunteer_records.c.last_name.asc(),
                volunteer_records.c.first_name.asc(),
                role_assignments.c.id.asc(),
            )
            .subquery()
        )
        members_select = select(
            literal("member").label("row_type"),
            literal(None, type_=BigInteger()).label("group_id"),
            literal(None, type_=Text()).label("group_name"),
            literal(None, type_=Text()).label("group_description"),
            literal(None, type_=Boolean()).label("group_active"),
            literal(None, type_=Integer()).label("group_active_until"),
            literal(None, type_=BigInteger()).label("group_parent_id"),
            literal(None, type_=Integer()).label("group_discount_step"),
            literal(None, type_=groups.c.created_at.type).label("group_created_at"),
            literal(None, type_=BigInteger()).label("position_id"),
            literal(None, type_=Text()).label("position_name"),
            literal(None, type_=Integer()).label("position_points"),
            literal(None, type_=Integer()).label("position_assignment_count"),
            recent_members.c.id.label("history_id"),
            recent_members.c.volunteer_id.label("volunteer_id"),
            recent_members.c.first_name.label("volunteer_first_name"),
            recent_members.c.last_name.label("volunteer_last_name"),
            recent_members.c.sha1.label("volunteer_photo_sha1"),
            recent_members.c.filetype.label("volunteer_photo_filetype"),
            recent_members.c.role_name.label("member_role_name"),
            recent_members.c.semester.label("member_semester"),
            recent_members.c.contract_signed.label("member_contract_signed"),
        )
        rows = await self.fetch_all_mappings(
            union_all(group_select, positions_select, members_select)
        )
        log_operation_timing(
            logger,
            operation="groups.detail",
            started_at=started_at,
            details={"group_id": group_id},
        )
        group_row = next((row for row in rows if row["row_type"] == "group"), None)
        if group_row is None:
            return None
        delete_blockers = await self._get_group_delete_blockers(group_id)
        positions, members = _build_group_detail_lists(rows)
        contact = await self.fetch_first_mapping(
            select(
                groups.c.recruitment_contact_email,
                groups.c.recruitment_contact_volunteer_id,
                volunteer_records.c.first_name,
                volunteer_records.c.last_name,
            )
            .select_from(
                groups.outerjoin(
                    volunteer_records,
                    volunteer_records.c.id == groups.c.recruitment_contact_volunteer_id,
                )
            )
            .where(groups.c.id == group_id)
        ) or {}
        contact_name = " ".join(
            filter(None, [contact.get("first_name"), contact.get("last_name")])
        ).strip()
        return GroupDetail(
            group_id=group_row["group_id"],
            name=group_row["group_name"],
            description=group_row["group_description"],
            active=group_row["group_active"],
            active_until_semester=group_row["group_active_until"],
            active_until_label=format_semester_code(group_row["group_active_until"]),
            parent_group_id=group_row["group_parent_id"],
            discount_step=group_row["group_discount_step"],
            created_at=coerce_datetime(group_row.get("group_created_at")),
            recruitment_contact_email=contact.get("recruitment_contact_email"),
            recruitment_contact_volunteer_id=contact.get("recruitment_contact_volunteer_id"),
            recruitment_contact_volunteer_name=contact_name or None,
            positions=sorted(
                positions,
                key=lambda item: ((item.role_name or "").lower(), item.role_id),
            ),
            recent_members=sorted(
                members,
                key=lambda item: (
                    (item.volunteer_name or "").lower(),
                    (item.role_name or "").lower(),
                    item.history_id,
                ),
            ),
            delete_blockers=delete_blockers,
        )

    async def get_group_history_by_semester(
        self, group_id: int, *, expanded_semesters: int | None = None
    ) -> list[SemesterGroup]:
        """Group history newest semester first.

        With *expanded_semesters*, only that many recent semesters carry their
        members; older ones carry a count and load on demand. Rendering every
        member of a long-lived group made this page several megabytes.
        """
        if expanded_semesters is None:
            grouped = await self._group_history_members(group_id)
            return [
                _semester_group(semester_code, members)
                for semester_code, members in sorted(grouped.items(), reverse=True)
            ]
        count_rows = await self.fetch_all_mappings(
            select(
                role_assignments.c.semester,
                func.count().label("member_count"),
            )
            .where(role_assignments.c.group_id == group_id)
            .group_by(role_assignments.c.semester)
            .order_by(role_assignments.c.semester.desc())
        )
        expanded = [row["semester"] for row in count_rows[:expanded_semesters]]
        grouped = (
            await self._group_history_members(group_id, semesters=expanded)
            if expanded
            else {}
        )
        return [
            SemesterGroup(
                semester_code=row["semester"],
                semester_label=format_semester_code(row["semester"])
                or str(row["semester"]),
                members=grouped.get(row["semester"], [])
                if row["semester"] in expanded
                else None,
                member_count=row["member_count"],
            )
            for row in count_rows
        ]

    async def get_group_history_semester(
        self, group_id: int, semester_code: int
    ) -> SemesterGroup:
        grouped = await self._group_history_members(group_id, semesters=[semester_code])
        return _semester_group(semester_code, grouped.get(semester_code, []))

    async def _group_history_members(
        self, group_id: int, *, semesters: list[int] | None = None
    ) -> dict[int, list[GroupMemberItem]]:
        stmt = (
            select(
                role_assignments.c.id,
                role_assignments.c.volunteer_id,
                role_assignments.c.semester,
                role_assignments.c.contract_signed,
                volunteer_records.c.first_name,
                volunteer_records.c.last_name,
                volunteer_photos.c.sha1,
                volunteer_photos.c.filetype,
                assignment_roles.c.name.label("role_name"),
            )
            .select_from(
                role_assignments.join(volunteer_records, volunteer_records.c.id == role_assignments.c.volunteer_id)
                .outerjoin(
                    volunteer_photos, volunteer_photos.c.volunteer_id == volunteer_records.c.id
                )
                .outerjoin(assignment_roles, assignment_roles.c.id == role_assignments.c.role_id)
            )
            .where(role_assignments.c.group_id == group_id)
            .order_by(
                role_assignments.c.semester.desc(),
                volunteer_records.c.last_name.asc(),
                volunteer_records.c.first_name.asc(),
                role_assignments.c.id.asc(),
            )
        )
        if semesters is not None:
            stmt = stmt.where(role_assignments.c.semester.in_(semesters))
        rows = await self.fetch_all_mappings(stmt)
        grouped_members: dict[int, list[GroupMemberItem]] = defaultdict(list)
        for row in rows:
            semester_code = row["semester"]
            grouped_members[semester_code].append(
                GroupMemberItem(
                    history_id=row["id"],
                    volunteer_id=row["volunteer_id"],
                    volunteer_name=build_full_name(
                        row.get("first_name"), row.get("last_name")
                    ),
                    photo_url=_build_group_member_photo_url(
                        row.get("sha1"), row.get("filetype")
                    ),
                    role_name=row.get("role_name"),
                    semester_code=semester_code,
                    semester_label=format_semester_code(semester_code)
                    or str(semester_code),
                    contract_signed=row["contract_signed"],
                )
            )
        return grouped_members

    async def get_group_semester_stats(self, group_id: int) -> list[SemesterStats]:
        stmt = (
            select(
                role_assignments.c.semester,
                func.count(distinct(role_assignments.c.volunteer_id)).label("member_count"),
            )
            .where(role_assignments.c.group_id == group_id)
            .group_by(role_assignments.c.semester)
            .order_by(role_assignments.c.semester.asc())
        )
        rows = await self.fetch_all_mappings(stmt)
        return [
            SemesterStats(
                semester_code=row["semester"],
                semester_label=format_semester_code(row["semester"])
                or str(row["semester"]),
                member_count=row["member_count"],
            )
            for row in rows
        ]

    async def get_current_group_member_counts(self) -> list[GroupMemberCount]:
        current_semester = get_current_semester_code()
        stmt = (
            select(
                groups.c.id,
                groups.c.name,
                func.count(distinct(role_assignments.c.volunteer_id)).label("member_count"),
            )
            .select_from(groups.join(role_assignments, role_assignments.c.group_id == groups.c.id))
            .where(role_assignments.c.semester == current_semester)
            .group_by(groups.c.id, groups.c.name)
            .order_by(
                func.count(distinct(role_assignments.c.volunteer_id)).desc(),
                groups.c.name.asc(),
            )
            .limit(25)
        )
        rows = await self.fetch_all_mappings(stmt)
        return [
            GroupMemberCount(
                group_id=row["id"],
                group_name=row["name"],
                member_count=row["member_count"],
            )
            for row in rows
        ]

    async def get_org_stats_detailed(self) -> list[OrgSemesterDetailed]:
        org_stmt = (
            select(
                role_assignments.c.semester,
                func.count(distinct(role_assignments.c.volunteer_id)).label("unique_members"),
            )
            .group_by(role_assignments.c.semester)
            .order_by(role_assignments.c.semester.asc())
        )
        breakdown_stmt = (
            select(
                role_assignments.c.semester,
                groups.c.name.label("group_name"),
                func.count(distinct(role_assignments.c.volunteer_id)).label("member_count"),
            )
            .select_from(role_assignments.join(groups, groups.c.id == role_assignments.c.group_id))
            .group_by(role_assignments.c.semester, groups.c.id, groups.c.name)
            .order_by(
                role_assignments.c.semester.asc(),
                func.count(distinct(role_assignments.c.volunteer_id)).desc(),
                groups.c.name.asc(),
            )
        )
        org_rows = await self.fetch_all_mappings(org_stmt)
        breakdown_rows = await self.fetch_all_mappings(breakdown_stmt)
        grouped_breakdown: dict[int, list[GroupBreakdownItem]] = defaultdict(list)
        for row in breakdown_rows:
            grouped_breakdown[row["semester"]].append(
                GroupBreakdownItem(
                    group_name=row["group_name"],
                    member_count=row["member_count"],
                )
            )
        return [
            OrgSemesterDetailed(
                semester_code=row["semester"],
                semester_label=format_semester_code(row["semester"])
                or str(row["semester"]),
                unique_members=row["unique_members"],
                groups=grouped_breakdown.get(row["semester"], []),
            )
            for row in org_rows
        ]

    async def get_org_retention_stats(self) -> list[SemesterRetentionStats]:
        current_members = (
            select(
                role_assignments.c.semester.label("semester"),
                role_assignments.c.volunteer_id.label("volunteer_id"),
            )
            .distinct()
            .subquery("current_members")
        )
        history_prev_any = role_assignments.alias("history_prev_any")
        history_next_any = role_assignments.alias("history_next_any")
        history_same_source = role_assignments.alias("history_same_source")
        history_next_same = role_assignments.alias("history_next_same")
        previous_semester = case(
            (current_members.c.semester % 10 == 1, current_members.c.semester - 9),
            else_=current_members.c.semester - 1,
        )
        next_semester = case(
            (current_members.c.semester % 10 == 1, current_members.c.semester + 1),
            else_=current_members.c.semester + 9,
        )
        retained_from_prev_exists = (
            select(literal(1))
            .select_from(history_prev_any)
            .where(
                history_prev_any.c.volunteer_id == current_members.c.volunteer_id,
                history_prev_any.c.semester == previous_semester,
            )
            .exists()
        )
        retained_to_next_exists = (
            select(literal(1))
            .select_from(history_next_any)
            .where(
                history_next_any.c.volunteer_id == current_members.c.volunteer_id,
                history_next_any.c.semester == next_semester,
            )
            .exists()
        )
        retained_to_next_same_group_exists = (
            select(literal(1))
            .select_from(
                history_same_source.join(
                    history_next_same,
                    (
                        history_next_same.c.volunteer_id
                        == history_same_source.c.volunteer_id
                    )
                    & (
                        history_next_same.c.group_id == history_same_source.c.group_id
                    ),
                )
            )
            .where(
                history_same_source.c.volunteer_id == current_members.c.volunteer_id,
                history_same_source.c.semester == current_members.c.semester,
                history_next_same.c.semester == next_semester,
            )
            .exists()
        )
        stmt = (
            select(
                current_members.c.semester,
                func.count().label("total_members"),
                func.sum(case((retained_from_prev_exists, 1), else_=0)).label(
                    "retained_from_prev"
                ),
                func.sum(case((retained_to_next_same_group_exists, 1), else_=0)).label(
                    "retained_to_next_same_group"
                ),
                func.sum(case((retained_to_next_exists, 1), else_=0)).label(
                    "retained_to_next"
                ),
            )
            .select_from(current_members)
            .group_by(current_members.c.semester)
            .order_by(current_members.c.semester.asc())
        )
        rows = await self.fetch_all_mappings(stmt)
        return [_map_retention_stat_row(row) for row in rows]

    async def get_group_retention_stats(
        self, group_id: int
    ) -> list[SemesterRetentionStats]:
        current_members = (
            select(
                role_assignments.c.semester.label("semester"),
                role_assignments.c.volunteer_id.label("volunteer_id"),
            )
            .where(role_assignments.c.group_id == group_id)
            .distinct()
            .subquery("current_group_members")
        )
        history_prev_same_group = role_assignments.alias("history_prev_same_group")
        history_next_same_group = role_assignments.alias("history_next_same_group")
        history_next_any = role_assignments.alias("history_next_any")
        previous_semester = case(
            (current_members.c.semester % 10 == 1, current_members.c.semester - 9),
            else_=current_members.c.semester - 1,
        )
        next_semester = case(
            (current_members.c.semester % 10 == 1, current_members.c.semester + 1),
            else_=current_members.c.semester + 9,
        )
        retained_from_prev_exists = (
            select(literal(1))
            .select_from(history_prev_same_group)
            .where(
                history_prev_same_group.c.volunteer_id == current_members.c.volunteer_id,
                history_prev_same_group.c.group_id == group_id,
                history_prev_same_group.c.semester == previous_semester,
            )
            .exists()
        )
        retained_to_next_same_group_exists = (
            select(literal(1))
            .select_from(history_next_same_group)
            .where(
                history_next_same_group.c.volunteer_id == current_members.c.volunteer_id,
                history_next_same_group.c.group_id == group_id,
                history_next_same_group.c.semester == next_semester,
            )
            .exists()
        )
        retained_to_next_exists = (
            select(literal(1))
            .select_from(history_next_any)
            .where(
                history_next_any.c.volunteer_id == current_members.c.volunteer_id,
                history_next_any.c.semester == next_semester,
            )
            .exists()
        )
        stmt = (
            select(
                current_members.c.semester,
                func.count().label("total_members"),
                func.sum(case((retained_from_prev_exists, 1), else_=0)).label(
                    "retained_from_prev"
                ),
                func.sum(case((retained_to_next_same_group_exists, 1), else_=0)).label(
                    "retained_to_next_same_group"
                ),
                func.sum(case((retained_to_next_exists, 1), else_=0)).label(
                    "retained_to_next"
                ),
            )
            .select_from(current_members)
            .group_by(current_members.c.semester)
            .order_by(current_members.c.semester.asc())
        )
        rows = await self.fetch_all_mappings(stmt)
        return [_map_retention_stat_row(row) for row in rows]

    async def _get_group_delete_blockers(self, group_id: int) -> list[str]:
        child_group_count = await self.fetch_scalar(
            select(func.count())
            .select_from(groups)
            .where(groups.c.parent_group_id == group_id)
        )
        history_count = await self.fetch_scalar(
            select(func.count())
            .select_from(role_assignments)
            .where(role_assignments.c.group_id == group_id)
        )
        blockers: list[str] = []
        if child_group_count:
            blockers.append("Gruppen har undergrupper.")
        if history_count:
            blockers.append("Gruppen har historikk og kan ikke slettes.")
        return blockers


def _map_retention_stat_row(row) -> SemesterRetentionStats:
    total_members = int(row["total_members"] or 0)
    retained_from_prev = int(row["retained_from_prev"] or 0)
    retained_to_next_same_group = int(row.get("retained_to_next_same_group") or 0)
    retained_to_next = int(row["retained_to_next"] or 0)
    retained_to_next_other_group = max(
        0, retained_to_next - retained_to_next_same_group
    )
    return SemesterRetentionStats(
        semester_code=row["semester"],
        semester_label=format_semester_code(row["semester"]) or str(row["semester"]),
        total_members=total_members,
        retained_from_prev=retained_from_prev,
        new_members=total_members - retained_from_prev,
        retained_to_next_same_group=retained_to_next_same_group,
        retained_to_next_other_group=retained_to_next_other_group,
        retained_to_next=retained_to_next,
        churned=total_members - retained_to_next,
    )


def _build_group_detail_lists(
    rows: list[dict[str, Any]],
) -> tuple[list[GroupPositionItem], list[GroupMemberItem]]:
    positions: list[GroupPositionItem] = []
    seen_positions: set[int] = set()
    members: list[GroupMemberItem] = []
    seen_members: set[int] = set()
    for row in rows:
        if (
            row["row_type"] == "position"
            and row["position_id"] is not None
            and row["position_id"] not in seen_positions
        ):
            seen_positions.add(row["position_id"])
            assignment_count = row["position_assignment_count"] or 0
            blockers = (
                ["Vervet har medlemmer og kan ikke slettes."]
                if assignment_count
                else []
            )
            positions.append(
                GroupPositionItem(
                    role_id=row["position_id"],
                    role_name=row["position_name"],
                    pingvin_points=row["position_points"],
                    assignment_count=assignment_count,
                    delete_blockers=blockers,
                )
            )
        if (
            row["row_type"] == "member"
            and row["history_id"] is not None
            and row["history_id"] not in seen_members
        ):
            seen_members.add(row["history_id"])
            members.append(
                GroupMemberItem(
                    history_id=row["history_id"],
                    volunteer_id=row["volunteer_id"],
                    volunteer_name=build_full_name(
                        row.get("volunteer_first_name"),
                        row.get("volunteer_last_name"),
                    ),
                    photo_url=_build_group_member_photo_url(
                        row.get("volunteer_photo_sha1"),
                        row.get("volunteer_photo_filetype"),
                    ),
                    role_name=row.get("member_role_name"),
                    semester_code=row["member_semester"],
                    semester_label=format_semester_code(row["member_semester"])
                    or str(row["member_semester"]),
                    contract_signed=row["member_contract_signed"],
                )
            )
    return positions, members


def _build_group_member_photo_url(sha1: str | None, filetype: str | None) -> str | None:
    if not sha1 or not filetype:
        return None
    return build_photo_media_url(f"{sha1}.{filetype}")


def _semester_group(semester_code: int, members: list[GroupMemberItem]) -> SemesterGroup:
    return SemesterGroup(
        semester_code=semester_code,
        semester_label=format_semester_code(semester_code) or str(semester_code),
        members=members,
        member_count=len(members),
    )
