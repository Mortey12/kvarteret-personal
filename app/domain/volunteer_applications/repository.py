from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    cast,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import SqlAlchemyRepository
from app.domain.admin_accounts.tables import group_admin_memberships, user_accounts
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.volunteer_applications.tables import (
    volunteer_application_friend_invitations,
    volunteer_application_invites,
    volunteer_application_submissions,
    volunteer_prospect_idempotency_keys,
    volunteer_prospect_submissions,
)
from app.domain.volunteers.tables import volunteer_photos, volunteer_records
from app.domain.volunteer_applications.state_machine import (
    ApplicationState,
    DomainEventRecord,
)
from app.domain.volunteer_applications.tables import domain_events
from app.domain.volunteer_applications.models import (
    PublicProspectGroup,
    PublicProspectRequestClaim,
    PublicProspectRegistrationResult,
    TrialApplicantCardSnapshot,
    VolunteerApplicationDetail,
    VolunteerApplicationFriendInvite,
    VolunteerApplicationFriendRelationship,
    VolunteerApplicationInvite,
    VolunteerApplicationSubmissionInput,
    VolunteerApplicationStatusEvent,
    VolunteerProspectIdempotencyConflictError,
)
from app.shared.phone_numbers import normalize_phone_number
from app.media_tokens import MediaTokenService
from app.observability import current_trace_id


class VolunteerApplicationsRepository(SqlAlchemyRepository):
    def __init__(self, media_token_service: MediaTokenService | None = None) -> None:
        self.media_token_service = media_token_service

    async def claim_public_prospect_request(
        self,
        *,
        idempotency_key: UUID,
        request_hash: str,
        now: datetime,
    ) -> PublicProspectRequestClaim:
        existing_hash = await self.session.scalar(
            select(volunteer_prospect_idempotency_keys.c.request_hash).where(
                volunteer_prospect_idempotency_keys.c.idempotency_key == idempotency_key
            )
        )
        if existing_hash is None:
            try:
                async with self.session.begin_nested():
                    await self.session.execute(
                        insert(volunteer_prospect_idempotency_keys).values(
                            idempotency_key=idempotency_key,
                            request_hash=request_hash,
                            created_at=now,
                        )
                    )
            except IntegrityError:
                pass
            existing_hash = await self.session.scalar(
                select(volunteer_prospect_idempotency_keys.c.request_hash).where(
                    volunteer_prospect_idempotency_keys.c.idempotency_key
                    == idempotency_key
                )
            )
        if existing_hash != request_hash:
            raise VolunteerProspectIdempotencyConflictError(
                "Idempotency key was already used with different request content."
            )

        existing_submission = (
            (
                await self.session.execute(
                    select(
                        volunteer_prospect_submissions.c.status,
                        volunteer_prospect_submissions.c.registration_id,
                        volunteer_prospect_submissions.c.registration_ids,
                    ).where(
                        volunteer_prospect_submissions.c.request_hash == request_hash
                    )
                )
            )
            .mappings()
            .first()
        )
        if existing_submission is None:
            try:
                async with self.session.begin_nested():
                    await self.session.execute(
                        insert(volunteer_prospect_submissions).values(
                            request_hash=request_hash,
                            status="processing",
                            registration_id=None,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                return PublicProspectRequestClaim(created=True)
            except IntegrityError:
                existing_submission = (
                    (
                        await self.session.execute(
                            select(
                                volunteer_prospect_submissions.c.status,
                                volunteer_prospect_submissions.c.registration_id,
                                volunteer_prospect_submissions.c.registration_ids,
                            ).where(
                                volunteer_prospect_submissions.c.request_hash
                                == request_hash
                            )
                        )
                    )
                    .mappings()
                    .first()
                )

        if (
            existing_submission is None
            or existing_submission["status"] != "completed"
            or existing_submission["registration_id"] is None
        ):
            raise RuntimeError("Volunteer prospect idempotency state is incomplete.")
        return PublicProspectRequestClaim(
            created=False,
            registration_id=int(existing_submission["registration_id"]),
            registration_ids=tuple(
                existing_submission["registration_ids"]
                or [existing_submission["registration_id"]]
            ),
        )

    async def complete_public_prospect_request(
        self,
        *,
        request_hash: str,
        registration_id: int,
        now: datetime,
        registration_ids: tuple[int, ...] = (),
    ) -> None:
        result = await self.session.execute(
            update(volunteer_prospect_submissions)
            .where(
                volunteer_prospect_submissions.c.request_hash == request_hash,
                volunteer_prospect_submissions.c.status == "processing",
            )
            .values(
                status="completed",
                registration_id=registration_id,
                registration_ids=list(registration_ids or (registration_id,)),
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("Volunteer prospect idempotency claim was lost.")

    async def create_public_prospect_registration(
        self,
        *,
        token: str,
        email: str,
        first_name: str | None,
        last_name: str,
        phone: str | None,
        study_institution: str | None,
        background_details: str | None,
        first_choice_label: str,
        second_choice_label: str | None,
        initial_group_id: int | None,
        initial_role_id: int | None,
        first_choice_group_id: int,
        second_choice_group_id: int | None,
        friend_invites: list[tuple[str, str]] | None = None,
        inviter_name: str | None = None,
        origin_trace_id: str | None = None,
    ) -> PublicProspectRegistrationResult:
        friend_invites = friend_invites or []
        created_friend_invites: list[VolunteerApplicationFriendInvite] = []
        session = self.session
        inserted = (
            (
                await session.execute(
                    insert(volunteer_application_invites)
                    .values(
                        token=token,
                        email=email,
                        source="public_signup",
                        status=ApplicationState.NEW,
                        initial_group_id=initial_group_id,
                        initial_role_id=initial_role_id,
                        first_choice_group_id=first_choice_group_id,
                        second_choice_group_id=second_choice_group_id,
                        first_choice_label=first_choice_label,
                        second_choice_label=second_choice_label,
                        trial_shift_attended=False,
                        origin_trace_id=origin_trace_id,
                    )
                    .returning(volunteer_application_invites.c.id)
                )
            )
            .mappings()
            .one()
        )
        await session.execute(
            insert(volunteer_application_submissions).values(
                invite_id=inserted["id"],
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=normalize_phone_number(phone),
                gender="A",
                studiested=study_institution,
                bakgrunn=background_details,
            )
        )
        for friend_email, friend_token in friend_invites:
            existing_friend = await self.find_active_registration_for_target(
                friend_email,
                group_id=first_choice_group_id,
                role_id=initial_role_id,
            )
            if existing_friend is not None:
                # Repeated invitations must not overwrite or leak a private application.
                continue
            friend_row = (
                (
                    await session.execute(
                        insert(volunteer_application_invites)
                        .values(
                            token=friend_token,
                            email=friend_email,
                            source="friend_invite",
                            status=ApplicationState.NEW,
                            initial_group_id=initial_group_id,
                            initial_role_id=initial_role_id,
                            first_choice_group_id=first_choice_group_id,
                            second_choice_group_id=second_choice_group_id,
                            first_choice_label=first_choice_label,
                            second_choice_label=second_choice_label,
                            trial_shift_attended=False,
                            origin_trace_id=origin_trace_id,
                        )
                        .returning(
                            volunteer_application_invites.c.id,
                            volunteer_application_invites.c.token,
                            volunteer_application_invites.c.email,
                        )
                    )
                )
                .mappings()
                .one()
            )
            await session.execute(
                insert(volunteer_application_friend_invitations).values(
                    inviter_application_id=inserted["id"],
                    invitee_application_id=friend_row["id"],
                    inviter_name_snapshot=inviter_name,
                    inviter_email_snapshot=email,
                    invitee_email_snapshot=friend_row["email"],
                    created_at=func.now(),
                )
            )
            created_friend_invites.append(
                VolunteerApplicationFriendInvite(
                    registration_id=friend_row["id"],
                    token=friend_row["token"],
                    email=friend_row["email"],
                    inviter_name=inviter_name or email,
                )
            )
        detail = await self.get_volunteer_application_detail(inserted["id"])
        assert detail is not None
        return PublicProspectRegistrationResult(
            detail=detail, friend_invites=created_friend_invites
        )

    async def create_volunteer_application_invitation(
        self,
        *,
        email: str,
        token: str,
        initial_group_id: int | None = None,
        initial_role_id: int | None = None,
    ) -> VolunteerApplicationInvite:
        session = self.session
        row = (
            (
                await session.execute(
                    insert(volunteer_application_invites)
                    .values(
                        token=token,
                        email=email,
                        source="invite",
                        status=ApplicationState.NEW,
                        initial_group_id=initial_group_id,
                        initial_role_id=initial_role_id,
                        trial_shift_attended=False,
                    )
                    .returning(
                        volunteer_application_invites.c.id,
                        volunteer_application_invites.c.token,
                        volunteer_application_invites.c.email,
                        volunteer_application_invites.c.created_at,
                        volunteer_application_invites.c.initial_group_id,
                        volunteer_application_invites.c.initial_role_id,
                    )
                )
            )
            .mappings()
            .one()
        )
        names = await self._fetch_assignment_names(
            session,
            initial_group_id=row["initial_group_id"],
            initial_role_id=row["initial_role_id"],
        )
        return VolunteerApplicationInvite(
            registration_id=row["id"],
            token=row["token"],
            email=row["email"],
            created_at=row["created_at"],
            initial_group_id=row["initial_group_id"],
            initial_group_name=names["group_name"],
            initial_role_id=row["initial_role_id"],
            initial_role_name=names["role_name"],
        )

    async def count_pending_volunteer_applications(self) -> int:
        stmt = (
            select(func.count())
            .select_from(
                volunteer_application_invites.join(
                    volunteer_application_submissions,
                    volunteer_application_submissions.c.invite_id
                    == volunteer_application_invites.c.id,
                )
            )
            .where(
                volunteer_application_invites.c.status.not_in(
                    [ApplicationState.VOLUNTEER, ApplicationState.NOT_VOLUNTEER]
                )
            )
        )
        session = self.session
        count = await session.scalar(stmt)
        return int(count or 0)

    async def get_volunteer_application_detail(
        self, registration_id: int
    ) -> VolunteerApplicationDetail | None:
        return await self._get_detail(
            select(volunteer_application_invites.c.id).where(
                volunteer_application_invites.c.id == registration_id
            )
        )

    async def get_volunteer_application_by_token(
        self, token: str
    ) -> VolunteerApplicationDetail | None:
        return await self._get_detail(
            select(volunteer_application_invites.c.id).where(
                volunteer_application_invites.c.token == token
            )
        )

    async def find_public_prospect_groups_by_slugs(
        self, slugs: list[str]
    ) -> dict[str, PublicProspectGroup]:
        if not slugs:
            return {}
        stmt = select(groups.c.id, groups.c.slug, groups.c.name).where(
            groups.c.slug.in_(slugs)
        )
        session = self.session
        rows = (await session.execute(stmt)).mappings().all()
        return {
            row["slug"]: PublicProspectGroup(
                group_id=row["id"],
                slug=row["slug"],
                name=row["name"],
            )
            for row in rows
        }

    async def find_public_prospect_role_id(
        self, *, group_id: int, role_name: str
    ) -> int | None:
        return await self.session.scalar(
            select(assignment_roles.c.id)
            .where(
                assignment_roles.c.group_id == group_id,
                assignment_roles.c.name == role_name,
            )
            .limit(1)
        )

    async def save_submission(
        self,
        *,
        registration_id: int,
        email: str,
        submission: VolunteerApplicationSubmissionInput,
        photo_sha1: str | None,
        photo_filetype: str | None,
    ) -> None:
        session = self.session
        registration_row = (
            (
                await session.execute(
                    select(
                        volunteer_application_invites.c.promoted_volunteer_id,
                        volunteer_application_invites.c.owns_volunteer_profile,
                    )
                    .where(volunteer_application_invites.c.id == registration_id)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if registration_row is None:
            return

        volunteer_id = registration_row["promoted_volunteer_id"]
        if volunteer_id is not None and registration_row["owns_volunteer_profile"]:
            await session.execute(
                update(volunteer_records)
                .where(volunteer_records.c.id == volunteer_id)
                .values(
                    first_name=submission.first_name,
                    last_name=submission.last_name,
                    email=email,
                    gender=submission.gender,
                    birth_date=submission.birth_date,
                    street_address=submission.address,
                    postal_code=submission.postal_code,
                    phone=normalize_phone_number(submission.phone),
                )
            )
            if photo_sha1 and photo_filetype:
                existing_photo = (
                    (
                        await session.execute(
                            select(volunteer_photos.c.volunteer_id)
                            .where(volunteer_photos.c.volunteer_id == volunteer_id)
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing_photo:
                    await session.execute(
                        update(volunteer_photos)
                        .where(volunteer_photos.c.volunteer_id == volunteer_id)
                        .values(sha1=photo_sha1, filetype=photo_filetype)
                    )
                else:
                    await session.execute(
                        insert(volunteer_photos).values(
                            volunteer_id=volunteer_id,
                            sha1=photo_sha1,
                            filetype=photo_filetype,
                        )
                    )

        existing_row = (
            (
                await session.execute(
                    select(
                        volunteer_application_submissions.c.id,
                        volunteer_application_submissions.c.studiested,
                        volunteer_application_submissions.c.bakgrunn,
                    )
                    .where(
                        volunteer_application_submissions.c.invite_id == registration_id
                    )
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        payload = {
            "first_name": submission.first_name,
            "last_name": submission.last_name,
            "email": email,
            "gender": submission.gender,
            "birth_date": submission.birth_date,
            "street_address": submission.address,
            "postal_code": submission.postal_code,
            "phone": normalize_phone_number(submission.phone),
            "photo_sha1": photo_sha1,
            "photo_filetype": photo_filetype,
            "studiested": existing_row["studiested"] if existing_row else None,
            "bakgrunn": existing_row["bakgrunn"] if existing_row else None,
        }
        if existing_row:
            await session.execute(
                update(volunteer_application_submissions)
                .where(volunteer_application_submissions.c.invite_id == registration_id)
                .values(**payload)
            )
        else:
            await session.execute(
                insert(volunteer_application_submissions).values(
                    invite_id=registration_id,
                    **payload,
                )
            )
        await session.execute(
            update(volunteer_application_invites)
            .where(volunteer_application_invites.c.id == registration_id)
            .values(full_profile_submitted_at=func.now())
        )

    async def set_application_status(
        self,
        registration_id: int,
        *,
        status: ApplicationState,
        start_trial: bool = False,
        volunteer_id: int | None = None,
        trial_assignment_id: int | None = None,
        owns_volunteer_profile: bool | None = None,
        expected_status: ApplicationState | None = None,
    ) -> bool:
        """Write the new status; return False if ``expected_status`` no longer holds."""
        values: dict[str, object] = {"status": status}
        if owns_volunteer_profile is not None:
            values["owns_volunteer_profile"] = owns_volunteer_profile
        if status == ApplicationState.NOT_VOLUNTEER:
            values["trial_assignment_id"] = None
        if volunteer_id is not None:
            values["promoted_volunteer_id"] = volunteer_id
        if start_trial:
            started_at = datetime.now(UTC)
            values.update(
                trial_assignment_id=trial_assignment_id,
                trial_started_at=started_at,
                trial_ends_at=started_at + timedelta(days=30),
            )
        stmt = (
            update(volunteer_application_invites)
            .where(volunteer_application_invites.c.id == registration_id)
            .values(**values)
        )
        if expected_status is not None:
            # Compare-and-set: a concurrent transition makes this a no-op.
            stmt = stmt.where(volunteer_application_invites.c.status == expected_status)
        result = await self.session.execute(stmt)
        return result.rowcount == 1

    async def photo_in_use(self, sha1: str) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    exists().where(volunteer_photos.c.sha1 == sha1)
                    | exists().where(
                        volunteer_application_submissions.c.photo_sha1 == sha1
                    )
                )
            )
        )

    async def lock_registration_emails(self, emails: list[str]) -> None:
        """Serialize registration writes until the request transaction commits."""
        if self.session.get_bind().dialect.name != "postgresql":
            return
        for email in sorted({email.strip().lower() for email in emails}):
            await self.session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended("volunteer-registration:" + email, 0)
                    )
                )
            )

    async def get_approved_volunteer_id(self, application_id: int) -> int | None:
        return await self.session.scalar(
            select(volunteer_application_invites.c.promoted_volunteer_id).where(
                volunteer_application_invites.c.id == application_id,
                volunteer_application_invites.c.status == ApplicationState.VOLUNTEER,
                volunteer_application_invites.c.promoted_at.is_not(None),
            )
        )

    async def find_volunteer_id_by_email(self, email: str) -> int | None:
        ids = list(
            await self.session.scalars(
                select(volunteer_records.c.id)
                .where(
                    func.lower(func.coalesce(volunteer_records.c.email, ""))
                    == email.strip().lower(),
                )
                .limit(2)
            )
        )
        if len(ids) > 1:
            from app.domain.volunteer_applications.models import (
                VolunteerApplicationConflictError,
            )

            raise VolunteerApplicationConflictError(
                "Flere personoppføringer har denne e-postadressen. Kontakt en administrator."
            )
        return ids[0] if ids else None

    async def find_active_registration_id_by_email(self, email: str) -> int | None:
        session = self.session
        return await session.scalar(
            select(volunteer_application_invites.c.id)
            .where(
                func.lower(func.coalesce(volunteer_application_invites.c.email, ""))
                == email.lower(),
                volunteer_application_invites.c.status != ApplicationState.VOLUNTEER,
                volunteer_application_invites.c.status
                != ApplicationState.NOT_VOLUNTEER,
            )
            .limit(1)
        )

    async def find_active_registration_for_target(
        self,
        email: str,
        *,
        group_id: int | None,
        role_id: int | None,
        exclude_id: int | None = None,
    ) -> int | None:
        stmt = select(volunteer_application_invites.c.id).where(
            func.lower(volunteer_application_invites.c.email) == email.strip().lower(),
            func.coalesce(
                volunteer_application_invites.c.initial_group_id,
                volunteer_application_invites.c.first_choice_group_id,
            ).is_not_distinct_from(group_id),
            volunteer_application_invites.c.initial_role_id.is_not_distinct_from(
                role_id
            ),
            volunteer_application_invites.c.status.not_in(
                [ApplicationState.VOLUNTEER, ApplicationState.NOT_VOLUNTEER]
            ),
        )
        if exclude_id is not None:
            stmt = stmt.where(volunteer_application_invites.c.id != exclude_id)
        return await self.session.scalar(
            stmt.order_by(volunteer_application_invites.c.id).limit(1)
        )

    async def has_target_membership(
        self,
        email: str,
        *,
        group_id: int,
        role_id: int | None,
        semester_code: int,
    ) -> bool:
        stmt = (
            select(role_assignments.c.id)
            .join(
                volunteer_records,
                volunteer_records.c.id == role_assignments.c.volunteer_id,
            )
            .where(
                func.lower(volunteer_records.c.email) == email.strip().lower(),
                role_assignments.c.group_id == group_id,
                role_assignments.c.semester == semester_code,
                role_assignments.c.contract_signed.is_(True),
            )
        )
        if role_id is not None:
            stmt = stmt.where(role_assignments.c.role_id == role_id)
        return await self.session.scalar(stmt.limit(1)) is not None

    async def get_friend_inviter(
        self, invitee_application_id: int
    ) -> VolunteerApplicationFriendRelationship | None:
        return (await self.list_friend_relationships([invitee_application_id])).get(
            invitee_application_id, (None, [])
        )[0]

    async def list_friend_invitees(
        self, inviter_application_id: int
    ) -> list[VolunteerApplicationFriendRelationship]:
        return (await self.list_friend_relationships([inviter_application_id])).get(
            inviter_application_id, (None, [])
        )[1]

    async def list_friend_relationships(
        self, application_ids: list[int]
    ) -> dict[
        int,
        tuple[
            VolunteerApplicationFriendRelationship | None,
            list[VolunteerApplicationFriendRelationship],
        ],
    ]:
        application_ids = list(dict.fromkeys(application_ids))
        relationships_by_application_id = {
            application_id: (None, []) for application_id in application_ids
        }
        if not application_ids:
            return relationships_by_application_id

        inviter_submission = volunteer_application_submissions.alias(
            "inviter_submission"
        )
        invitee_submission = volunteer_application_submissions.alias(
            "invitee_submission"
        )
        friend_invitation = volunteer_application_friend_invitations
        stmt = (
            select(
                friend_invitation.c.id,
                friend_invitation.c.inviter_application_id,
                friend_invitation.c.invitee_application_id,
                friend_invitation.c.inviter_name_snapshot,
                friend_invitation.c.inviter_email_snapshot,
                friend_invitation.c.invitee_email_snapshot,
                friend_invitation.c.created_at,
                friend_invitation.c.legacy_dropped_at,
                friend_invitation.c.legacy_dropped_by_user_account_id,
                inviter_submission.c.first_name.label("inviter_first_name"),
                inviter_submission.c.last_name.label("inviter_last_name"),
                invitee_submission.c.first_name.label("invitee_first_name"),
                invitee_submission.c.last_name.label("invitee_last_name"),
            )
            .select_from(
                friend_invitation.outerjoin(
                    inviter_submission,
                    inviter_submission.c.invite_id
                    == friend_invitation.c.inviter_application_id,
                ).outerjoin(
                    invitee_submission,
                    invitee_submission.c.invite_id
                    == friend_invitation.c.invitee_application_id,
                )
            )
            .where(
                or_(
                    friend_invitation.c.inviter_application_id.in_(application_ids),
                    friend_invitation.c.invitee_application_id.in_(application_ids),
                )
            )
            .order_by(friend_invitation.c.id.asc())
        )
        rows = (await self.session.execute(stmt)).mappings().all()
        for row in rows:
            relationship = _friend_relationship_from_row(row)
            if relationship.invitee_application_id in relationships_by_application_id:
                _, friend_invitees = relationships_by_application_id[
                    relationship.invitee_application_id
                ]
                relationships_by_application_id[relationship.invitee_application_id] = (
                    relationship,
                    friend_invitees,
                )
            if relationship.inviter_application_id in relationships_by_application_id:
                invited_by, friend_invitees = relationships_by_application_id[
                    relationship.inviter_application_id
                ]
                relationships_by_application_id[relationship.inviter_application_id] = (
                    invited_by,
                    [*friend_invitees, relationship],
                )
        return relationships_by_application_id

    async def list_group_admin_email_recipients(self, group_id: int) -> list[str]:
        stmt = (
            select(func.lower(user_accounts.c.email).label("email"))
            .select_from(
                group_admin_memberships.join(
                    user_accounts,
                    user_accounts.c.auth_user_id
                    == group_admin_memberships.c.auth_user_id,
                )
            )
            .where(
                group_admin_memberships.c.group_id == group_id,
                user_accounts.c.email.is_not(None),
                user_accounts.c.email != "",
            )
            .distinct()
            .order_by(func.lower(user_accounts.c.email))
        )
        session = self.session
        return [row for row in await session.scalars(stmt)]

    async def role_matches_group(self, *, role_id: int, group_id: int) -> bool:
        return bool(
            await self.fetch_scalar(
                select(
                    exists().where(
                        assignment_roles.c.id == role_id,
                        assignment_roles.c.group_id == group_id,
                    )
                )
            )
        )

    async def mark_promoted(
        self,
        *,
        registration_id: int,
        volunteer_id: int,
        accepted_group_id: int,
    ) -> None:
        await self.execute(
            update(volunteer_application_invites)
            .where(volunteer_application_invites.c.id == registration_id)
            .values(
                promoted_volunteer_id=volunteer_id,
                promoted_at=func.now(),
                status=ApplicationState.VOLUNTEER,
                initial_group_id=accepted_group_id,
            )
        )

    async def find_active_trial_applicant_by_email(
        self, email: str
    ) -> TrialApplicantCardSnapshot | None:
        return await self._find_active_trial_applicant(email=email)

    async def get_active_trial_applicant(
        self, application_id: int
    ) -> TrialApplicantCardSnapshot | None:
        return await self._find_active_trial_applicant(application_id=application_id)

    async def _find_active_trial_applicant(
        self,
        *,
        email: str | None = None,
        application_id: int | None = None,
    ) -> TrialApplicantCardSnapshot | None:
        accepted_group = groups.alias("trial_group")
        accepted_role = assignment_roles.alias("trial_role")
        stmt = (
            select(
                volunteer_application_invites.c.id,
                volunteer_application_invites.c.promoted_volunteer_id,
                volunteer_application_invites.c.created_at,
                volunteer_application_invites.c.trial_ends_at,
                func.coalesce(
                    volunteer_records.c.first_name,
                    volunteer_application_submissions.c.first_name,
                ).label("first_name"),
                func.coalesce(
                    volunteer_records.c.last_name,
                    volunteer_application_submissions.c.last_name,
                ).label("last_name"),
                volunteer_records.c.birth_date.label("linked_birth_date"),
                volunteer_application_submissions.c.birth_date,
                func.coalesce(
                    volunteer_photos.c.sha1,
                    volunteer_application_submissions.c.photo_sha1,
                ).label("photo_sha1"),
                func.coalesce(
                    volunteer_photos.c.filetype,
                    volunteer_application_submissions.c.photo_filetype,
                ).label("photo_filetype"),
                accepted_group.c.name.label("group_name"),
                accepted_group.c.discount_tier,
                accepted_role.c.name.label("role_name"),
            )
            .select_from(
                volunteer_application_invites.outerjoin(
                    volunteer_application_submissions,
                    volunteer_application_submissions.c.invite_id
                    == volunteer_application_invites.c.id,
                )
                .outerjoin(
                    volunteer_records,
                    volunteer_records.c.id
                    == volunteer_application_invites.c.promoted_volunteer_id,
                )
                .outerjoin(
                    volunteer_photos,
                    volunteer_photos.c.volunteer_id == volunteer_records.c.id,
                )
                .outerjoin(
                    accepted_group,
                    accepted_group.c.id
                    == func.coalesce(
                        volunteer_application_invites.c.initial_group_id,
                        volunteer_application_invites.c.first_choice_group_id,
                    ),
                )
                .outerjoin(
                    accepted_role,
                    accepted_role.c.id
                    == volunteer_application_invites.c.initial_role_id,
                )
            )
            .where(
                volunteer_application_invites.c.status == ApplicationState.TRIAL,
                volunteer_application_invites.c.trial_ends_at > func.now(),
            )
            .order_by(
                volunteer_application_invites.c.trial_ends_at.desc(),
                volunteer_application_invites.c.id.asc(),
            )
            .limit(1)
        )
        if email is not None:
            stmt = stmt.where(
                func.lower(volunteer_application_invites.c.email) == email.lower()
            )
        if application_id is not None:
            stmt = stmt.where(volunteer_application_invites.c.id == application_id)
        row = await self.fetch_first_mapping(stmt)
        if row is None:
            return None
        return TrialApplicantCardSnapshot(
            application_id=row["id"],
            volunteer_id=row["promoted_volunteer_id"],
            first_name=row["first_name"] or "",
            last_name=row["last_name"] or "",
            birth_date=row["linked_birth_date"]
            or (
                row["birth_date"].date()
                if isinstance(row["birth_date"], datetime)
                else row["birth_date"]
            ),
            created_at=row["created_at"],
            trial_ends_at=row["trial_ends_at"],
            photo_path=(
                f"{row['photo_sha1']}.{row['photo_filetype']}"
                if row["photo_sha1"] and row["photo_filetype"]
                else None
            ),
            group_name=row["group_name"] or "Kvarteret",
            role_name=row["role_name"] or "Prøvefrivillig",
            discount_level=row["discount_tier"],
        )

    async def delete_volunteer_application(self, registration_id: int) -> None:
        session = self.session
        array_function = (
            func.json_array_elements_text
            if session.get_bind().dialect.name == "postgresql"
            else func.json_each
        )
        batch = array_function(
            volunteer_prospect_submissions.c.registration_ids
        ).table_valued("value")
        # The secondary application is not covered by the primary-ID foreign key.
        await session.execute(
            delete(volunteer_prospect_submissions).where(
                (volunteer_prospect_submissions.c.registration_id == registration_id)
                | select(1)
                .select_from(batch)
                .where(cast(batch.c.value, BigInteger) == registration_id)
                .exists()
            )
        )
        await session.execute(
            delete(volunteer_application_submissions).where(
                volunteer_application_submissions.c.invite_id == registration_id
            )
        )
        await session.execute(
            delete(volunteer_application_invites).where(
                volunteer_application_invites.c.id == registration_id
            )
        )

    async def append_domain_event(
        self, event: DomainEventRecord, *, subject_id: int
    ) -> int:
        """Append one audit row; runs in the ambient request transaction
        so the event commits or rolls back with the state change it records."""
        result = await self.session.execute(
            insert(domain_events)
            .values(
                event_type=event.event_type,
                actor_user_account_id=event.actor_user_account_id,
                subject_type=event.subject_type,
                subject_id=subject_id,
                payload=event.payload,
                occurred_at=event.occurred_at,
                trace_id=event.trace_id or current_trace_id(),
            )
            .returning(domain_events.c.id)
        )
        return int(result.scalar_one())

    async def _get_detail(self, id_query) -> VolunteerApplicationDetail | None:
        accepted_group = groups.alias("accepted_group")
        first_choice_group = groups.alias("first_choice_group")
        second_choice_group = groups.alias("second_choice_group")
        accepted_role = assignment_roles.alias("accepted_role")
        detail_stmt = (
            select(
                volunteer_application_invites.c.id,
                volunteer_application_invites.c.token,
                volunteer_application_invites.c.email,
                volunteer_application_invites.c.created_at,
                volunteer_application_invites.c.source,
                volunteer_application_invites.c.status,
                volunteer_application_invites.c.initial_group_id,
                volunteer_application_invites.c.initial_role_id,
                volunteer_application_invites.c.first_choice_group_id,
                volunteer_application_invites.c.second_choice_group_id,
                volunteer_application_invites.c.trial_shift_attended,
                volunteer_application_invites.c.trial_shift_marked_at,
                volunteer_application_invites.c.trial_started_at,
                volunteer_application_invites.c.trial_ends_at,
                volunteer_application_invites.c.full_profile_submitted_at,
                volunteer_application_invites.c.promoted_volunteer_id,
                volunteer_application_invites.c.promoted_at,
                volunteer_application_invites.c.trial_assignment_id,
                volunteer_application_invites.c.owns_volunteer_profile,
                volunteer_application_invites.c.origin_trace_id,
                volunteer_application_submissions.c.id.label("pending_volunteer_id"),
                volunteer_application_submissions.c.first_name,
                volunteer_application_submissions.c.last_name,
                volunteer_application_submissions.c.phone,
                volunteer_application_submissions.c.birth_date,
                volunteer_application_submissions.c.gender,
                volunteer_application_submissions.c.street_address,
                volunteer_application_submissions.c.postal_code,
                volunteer_application_submissions.c.photo_sha1,
                volunteer_application_submissions.c.photo_filetype,
                volunteer_application_submissions.c.studiested,
                volunteer_application_submissions.c.bakgrunn,
                accepted_group.c.name.label("initial_group_name"),
                accepted_role.c.name.label("initial_role_name"),
                func.coalesce(
                    volunteer_application_invites.c.first_choice_label,
                    first_choice_group.c.name,
                ).label("first_choice_group_name"),
                func.coalesce(
                    volunteer_application_invites.c.second_choice_label,
                    second_choice_group.c.name,
                ).label("second_choice_group_name"),
            )
            .select_from(
                volunteer_application_invites.outerjoin(
                    volunteer_application_submissions,
                    volunteer_application_submissions.c.invite_id
                    == volunteer_application_invites.c.id,
                )
                .outerjoin(
                    accepted_group,
                    accepted_group.c.id
                    == volunteer_application_invites.c.initial_group_id,
                )
                .outerjoin(
                    accepted_role,
                    accepted_role.c.id
                    == volunteer_application_invites.c.initial_role_id,
                )
                .outerjoin(
                    first_choice_group,
                    first_choice_group.c.id
                    == volunteer_application_invites.c.first_choice_group_id,
                )
                .outerjoin(
                    second_choice_group,
                    second_choice_group.c.id
                    == volunteer_application_invites.c.second_choice_group_id,
                )
            )
            .where(volunteer_application_invites.c.id.in_(id_query))
            .limit(1)
        )
        session = self.session
        row = (await session.execute(detail_stmt)).mappings().first()
        if row is None:
            return None
        invited_by = await self.get_friend_inviter(row["id"])
        friend_invitees = await self.list_friend_invitees(row["id"])
        status_history = await self.list_status_history(row["id"])
        return VolunteerApplicationDetail(
            registration_id=row["id"],
            token=row["token"],
            email=row["email"],
            created_at=row["created_at"],
            submitted=row["full_profile_submitted_at"] is not None,
            source=row["source"],
            status=row["status"],
            pending_volunteer_id=row["pending_volunteer_id"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            phone=row["phone"],
            birth_date=row["birth_date"],
            gender=row["gender"],
            address=row["street_address"],
            postal_code=row["postal_code"],
            photo_sha1=row["photo_sha1"],
            photo_filetype=row["photo_filetype"],
            photo_url=(
                self.media_token_service.build_photo_media_url(
                    f"{row['photo_sha1']}.{row['photo_filetype']}"
                )
                if row["photo_sha1"]
                and row["photo_filetype"]
                and self.media_token_service is not None
                else None
            ),
            study_institution=row["studiested"],
            background_details=row["bakgrunn"],
            initial_group_id=row["initial_group_id"],
            initial_group_name=row["initial_group_name"],
            initial_role_id=row["initial_role_id"],
            initial_role_name=row["initial_role_name"],
            first_choice_group_id=row["first_choice_group_id"],
            first_choice_group_name=row["first_choice_group_name"],
            second_choice_group_id=row["second_choice_group_id"],
            second_choice_group_name=row["second_choice_group_name"],
            trial_shift_attended=bool(row["trial_shift_attended"]),
            trial_shift_marked_at=row["trial_shift_marked_at"],
            trial_started_at=row["trial_started_at"],
            trial_ends_at=row["trial_ends_at"],
            promoted_volunteer_id=row["promoted_volunteer_id"],
            promoted_at=row["promoted_at"],
            trial_assignment_id=row["trial_assignment_id"],
            owns_volunteer_profile=row["owns_volunteer_profile"],
            invited_by=invited_by,
            friend_invitees=friend_invitees,
            status_history=status_history,
            origin_trace_id=row["origin_trace_id"],
        )

    async def list_status_history(
        self, registration_id: int
    ) -> list[VolunteerApplicationStatusEvent]:
        event_statuses = {
            "prospect_registered": ApplicationState.NEW.value,
            "application_invited": ApplicationState.NEW.value,
            "application_contacted": ApplicationState.CONTACTED.value,
            "trial_started": ApplicationState.TRIAL.value,
            "application_approved": ApplicationState.VOLUNTEER.value,
            "application_rejected": ApplicationState.NOT_VOLUNTEER.value,
            "application_reopened": ApplicationState.CONTACTED.value,
            "application_volunteer_restored": ApplicationState.VOLUNTEER.value,
        }
        actor = user_accounts.alias("event_actor")
        rows = await self.fetch_all_mappings(
            select(
                domain_events.c.event_type,
                domain_events.c.actor_user_account_id,
                domain_events.c.occurred_at,
                actor.c.display_name,
                actor.c.username,
            )
            .select_from(
                domain_events.outerjoin(
                    actor, actor.c.id == domain_events.c.actor_user_account_id
                )
            )
            .where(
                domain_events.c.subject_type == "application",
                domain_events.c.subject_id == registration_id,
                domain_events.c.event_type.in_(tuple(event_statuses)),
            )
            .order_by(domain_events.c.occurred_at.desc(), domain_events.c.id.desc())
        )
        return [
            VolunteerApplicationStatusEvent(
                event_type=row["event_type"],
                status=event_statuses[row["event_type"]],
                actor_display_name=row["display_name"] or row["username"],
                actor_user_account_id=row["actor_user_account_id"],
                occurred_at=row["occurred_at"],
            )
            for row in rows
        ]

    async def _fetch_assignment_names(
        self,
        session: AsyncSession,
        *,
        initial_group_id: int | None,
        initial_role_id: int | None,
    ) -> dict[str, str | None]:
        if initial_group_id is None and initial_role_id is None:
            return {"group_name": None, "role_name": None}
        row = (
            (
                await session.execute(
                    select(
                        groups.c.name.label("group_name"),
                        assignment_roles.c.name.label("role_name"),
                    )
                    .select_from(
                        groups.outerjoin(
                            assignment_roles, assignment_roles.c.id == initial_role_id
                        )
                    )
                    .where(groups.c.id == initial_group_id)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return {"group_name": None, "role_name": None}
        return {"group_name": row["group_name"], "role_name": row["role_name"]}


def _friend_relationship_from_row(row) -> VolunteerApplicationFriendRelationship:
    inviter_name = _join_name(row["inviter_first_name"], row["inviter_last_name"])
    invitee_name = _join_name(row["invitee_first_name"], row["invitee_last_name"])
    return VolunteerApplicationFriendRelationship(
        relationship_id=row["id"],
        inviter_application_id=row["inviter_application_id"],
        invitee_application_id=row["invitee_application_id"],
        inviter_name=inviter_name or row["inviter_name_snapshot"],
        inviter_email=row["inviter_email_snapshot"],
        invitee_name=invitee_name,
        invitee_email=row["invitee_email_snapshot"],
        created_at=row["created_at"],
        legacy_dropped_at=row["legacy_dropped_at"],
        legacy_dropped_by_user_account_id=row["legacy_dropped_by_user_account_id"],
    )


def _join_name(first_name: str | None, last_name: str | None) -> str | None:
    name = " ".join(
        part.strip() for part in (first_name, last_name) if part and part.strip()
    )
    return name or None
