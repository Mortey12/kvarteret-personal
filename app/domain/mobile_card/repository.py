from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import logging
from time import perf_counter

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.repository import SqlAlchemyRepository
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.volunteers.tables import volunteer_photos, volunteer_records
from app.domain.volunteer_applications.tables import volunteer_application_invites
from app.domain.mobile_card.tables import (
    mobile_card_access_codes,
    mobile_card_refresh_tokens,
    mobile_card_trial_access_codes,
)
from app.observability import log_operation_timing

logger = logging.getLogger("app.performance")


@dataclass(slots=True)
class MobileCardRoleSnapshot:
    name: str
    group: str
    group_id: int
    discount_level: int | None
    pingvin_points: int
    signed_contract: bool


@dataclass(slots=True)
class MobileCardRoleHistorySnapshot:
    name: str
    group: str
    group_id: int
    discount_level: int | None
    pingvin_points: int
    signed_contract: bool
    semester: int
    is_active: bool


@dataclass(slots=True)
class MobileCardSnapshot:
    volunteer_id: int
    first_name: str
    last_name: str
    birth_date: date | None
    created_at: datetime
    photo_path: str | None
    pingvin_points: int
    active_roles: list[MobileCardRoleSnapshot]
    role_history: list[MobileCardRoleHistorySnapshot] = field(default_factory=list)


class MobileCardRepository(SqlAlchemyRepository):
    @staticmethod
    def _not_blocked_volunteer(volunteer_id):
        approved = (
            select(volunteer_application_invites.c.id)
            .where(
                volunteer_application_invites.c.promoted_volunteer_id == volunteer_id,
                volunteer_application_invites.c.status == "volunteer",
            )
            .exists()
        )
        created_by_application = (
            select(volunteer_application_invites.c.id)
            .where(
                volunteer_application_invites.c.promoted_volunteer_id == volunteer_id,
                volunteer_application_invites.c.owns_volunteer_profile.is_(True),
            )
            .exists()
        )
        return approved | ~created_by_application

    async def find_volunteers_by_email(self, email: str) -> list[dict]:
        stmt = (
            select(
                volunteer_records.c.id,
                volunteer_records.c.first_name,
                volunteer_records.c.last_name,
            )
            .where(func.lower(func.coalesce(volunteer_records.c.email, "")) == email)
            .where(self._not_blocked_volunteer(volunteer_records.c.id))
            .order_by(volunteer_records.c.id.asc())
        )
        return await self.fetch_all_mappings(stmt)

    async def store_access_code(
        self, *, volunteer_id: int, code_hash: str, created_at: datetime
    ) -> None:
        stmt = pg_insert(mobile_card_access_codes).values(
            volunteer_id=volunteer_id,
            code_hash=code_hash,
            created_at=created_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[mobile_card_access_codes.c.volunteer_id],
            set_={"code_hash": code_hash, "created_at": created_at},
        )
        session = self.session
        await session.execute(stmt)

    async def store_trial_access_code(
        self, *, application_id: int, code_hash: str, created_at: datetime
    ) -> None:
        stmt = pg_insert(mobile_card_trial_access_codes).values(
            application_id=application_id,
            code_hash=code_hash,
            created_at=created_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[mobile_card_trial_access_codes.c.application_id],
            set_={"code_hash": code_hash, "created_at": created_at},
        )
        await self.session.execute(stmt)

    async def store_refresh_token(
        self,
        *,
        token_hash: str,
        volunteer_id: int | None,
        application_id: int | None,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        await self.session.execute(
            pg_insert(mobile_card_refresh_tokens).values(
                token_hash=token_hash,
                volunteer_id=volunteer_id,
                application_id=application_id,
                created_at=created_at,
                expires_at=expires_at,
            )
        )

    async def use_refresh_token(
        self, *, token_hash: str, now: datetime, new_expires_at: datetime
    ) -> dict | None:
        """Atomically validate a refresh token and slide its expiry."""
        row = (
            await self.session.execute(
                update(mobile_card_refresh_tokens)
                .where(
                    mobile_card_refresh_tokens.c.token_hash == token_hash,
                    mobile_card_refresh_tokens.c.expires_at > now,
                )
                .values(expires_at=new_expires_at)
                .returning(
                    mobile_card_refresh_tokens.c.volunteer_id,
                    mobile_card_refresh_tokens.c.application_id,
                )
            )
        ).mappings().first()
        return dict(row) if row is not None else None

    async def consume_trial_access_code(
        self,
        *,
        application_id: int,
        code_hash: str,
        expires_after: datetime,
    ) -> bool:
        # Delete with all predicates so concurrent attempts cannot both consume it.
        deleted = await self.session.scalar(
            delete(mobile_card_trial_access_codes)
            .where(
                mobile_card_trial_access_codes.c.application_id == application_id,
                mobile_card_trial_access_codes.c.code_hash == code_hash,
                mobile_card_trial_access_codes.c.created_at >= expires_after,
            )
            .returning(mobile_card_trial_access_codes.c.application_id)
        )
        return deleted is not None

    async def find_trial_application_for_code(
        self,
        *,
        email: str,
        code_hash: str,
        expires_after: datetime,
    ) -> int | None:
        return await self.session.scalar(
            select(mobile_card_trial_access_codes.c.application_id)
            .join(
                volunteer_application_invites,
                volunteer_application_invites.c.id
                == mobile_card_trial_access_codes.c.application_id,
            )
            .where(
                func.lower(volunteer_application_invites.c.email)
                == email.strip().lower(),
                volunteer_application_invites.c.status == "trial",
                # Expired trials may still log in; the card is flagged instead.
                volunteer_application_invites.c.trial_ends_at.is_not(None),
                mobile_card_trial_access_codes.c.code_hash == code_hash,
                mobile_card_trial_access_codes.c.created_at >= expires_after,
            )
            .order_by(mobile_card_trial_access_codes.c.created_at.desc())
            .limit(1)
        )

    async def find_volunteer_by_email_and_code(
        self,
        *,
        email: str,
        code_hash: str,
        expires_after: datetime,
    ) -> dict | None:
        session = self.session
        row = (
            (
                await session.execute(
                    select(volunteer_records.c.id)
                    .select_from(
                        volunteer_records.join(
                            mobile_card_access_codes,
                            mobile_card_access_codes.c.volunteer_id
                            == volunteer_records.c.id,
                        )
                    )
                    .where(
                        func.lower(func.coalesce(volunteer_records.c.email, ""))
                        == email
                    )
                    .where(self._not_blocked_volunteer(volunteer_records.c.id))
                    .where(mobile_card_access_codes.c.code_hash == code_hash)
                    .where(mobile_card_access_codes.c.created_at >= expires_after)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        # Codes are single-use: consuming one deletes the row
        # (the columns are NOT NULL, so nulling them would raise).
        await session.execute(
            delete(mobile_card_access_codes).where(
                mobile_card_access_codes.c.volunteer_id == row["id"]
            )
        )
        return dict(row)

    async def fetch_card_snapshot(
        self,
        *,
        volunteer_id: int,
        semester_code: int,
        include_role_history: bool = False,
    ) -> MobileCardSnapshot | None:
        started_at = perf_counter()
        points_stmt = (
            select(func.coalesce(func.sum(assignment_roles.c.penguin_points), 0))
            .select_from(
                role_assignments.outerjoin(
                    assignment_roles,
                    assignment_roles.c.id == role_assignments.c.role_id,
                )
            )
            .where(role_assignments.c.volunteer_id == volunteer_id)
            .scalar_subquery()
        )
        snapshot_stmt = (
            select(
                volunteer_records.c.id,
                volunteer_records.c.first_name,
                volunteer_records.c.last_name,
                volunteer_records.c.birth_date,
                volunteer_records.c.created_at,
                volunteer_photos.c.sha1,
                volunteer_photos.c.filetype,
                points_stmt.label("pingvin_points"),
                assignment_roles.c.name.label("verv_navn"),
                groups.c.name.label("gruppe_navn"),
                role_assignments.c.group_id.label("gruppe_id"),
                groups.c.discount_tier,
                assignment_roles.c.penguin_points.label("pingvin_poeng"),
                role_assignments.c.contract_signed,
            )
            .select_from(
                volunteer_records.outerjoin(
                    volunteer_photos,
                    volunteer_photos.c.volunteer_id == volunteer_records.c.id,
                )
                .outerjoin(
                    role_assignments,
                    (role_assignments.c.volunteer_id == volunteer_records.c.id)
                    & (role_assignments.c.semester == semester_code),
                )
                .outerjoin(
                    assignment_roles,
                    assignment_roles.c.id == role_assignments.c.role_id,
                )
                .outerjoin(groups, groups.c.id == role_assignments.c.group_id)
            )
            .where(volunteer_records.c.id == volunteer_id)
            .where(self._not_blocked_volunteer(volunteer_records.c.id))
            .order_by(
                groups.c.name.asc().nullslast(),
                assignment_roles.c.name.asc().nullslast(),
            )
        )
        history_stmt = (
            select(
                role_assignments.c.id,
                role_assignments.c.semester,
                role_assignments.c.group_id.label("gruppe_id"),
                groups.c.name.label("gruppe_navn"),
                assignment_roles.c.name.label("verv_navn"),
                groups.c.discount_tier,
                assignment_roles.c.penguin_points.label("pingvin_poeng"),
                role_assignments.c.contract_signed,
            )
            .select_from(
                role_assignments.join(
                    groups, groups.c.id == role_assignments.c.group_id
                ).outerjoin(
                    assignment_roles,
                    assignment_roles.c.id == role_assignments.c.role_id,
                )
            )
            .where(role_assignments.c.volunteer_id == volunteer_id)
            .order_by(role_assignments.c.semester.desc(), role_assignments.c.id.desc())
        )
        session = self.session
        rows = list((await session.execute(snapshot_stmt)).mappings().all())
        history_rows = (
            list((await session.execute(history_stmt)).mappings().all())
            if include_role_history
            else []
        )
        log_operation_timing(
            logger,
            operation="mobile_card.snapshot",
            started_at=started_at,
            details={"volunteer_id": volunteer_id, "semester_code": semester_code},
        )
        if not rows:
            return None
        person_row = rows[0]

        photo_path = None
        if person_row["sha1"] and person_row["filetype"]:
            photo_path = f"{person_row['sha1']}.{person_row['filetype']}"

        return MobileCardSnapshot(
            volunteer_id=person_row["id"],
            first_name=person_row["first_name"] or "",
            last_name=person_row["last_name"],
            birth_date=person_row["birth_date"],
            created_at=person_row["created_at"],
            photo_path=photo_path,
            pingvin_points=int(person_row["pingvin_points"] or 0),
            active_roles=[
                MobileCardRoleSnapshot(
                    # An assignment may have no role (role_id is nullable); it still
                    # carries the group's discount tier, as the admin volunteer page counts it.
                    name=row["verv_navn"] or "Medlem",
                    group=row["gruppe_navn"],
                    group_id=row["gruppe_id"],
                    discount_level=row["discount_tier"],
                    pingvin_points=int(row["pingvin_poeng"] or 0),
                    signed_contract=bool(row["contract_signed"]),
                )
                for row in rows
                if row["gruppe_navn"] is not None
            ],
            role_history=[
                MobileCardRoleHistorySnapshot(
                    name=row["verv_navn"] or "",
                    group=row["gruppe_navn"],
                    group_id=row["gruppe_id"],
                    discount_level=row["discount_tier"],
                    pingvin_points=int(row["pingvin_poeng"] or 0),
                    signed_contract=row["contract_signed"],
                    semester=int(row["semester"]),
                    is_active=int(row["semester"]) == semester_code,
                )
                for row in history_rows
                if row["gruppe_navn"] is not None
            ],
        )
