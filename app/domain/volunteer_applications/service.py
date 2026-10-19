from __future__ import annotations

import logging
import re
from asyncio import to_thread
from datetime import UTC, datetime
from secrets import token_hex, token_urlsafe
from uuid import UUID

from app.config import Settings
from app.errors import NotConfiguredError
from app.infrastructure.media.protocols import PhotoProcessorProtocol
from app.shared.phone_numbers import (
    normalize_phone_number,
    normalize_required_phone_number,
)
from app.shared.semester import get_current_semester_code
from app.infrastructure.storage.protocols import StorageProtocol
from app.domain.volunteer_applications.side_effects import (
    VolunteerApplicationSideEffects,
)
from app.domain.volunteer_applications.state_machine import (
    ApplicationState,
    DomainEventRecord,
    IllegalTransition,
)
from app.domain.volunteer_applications.models import (
    ActiveVolunteerRegistrationExistsError,
    PublicProspectRegistrationInput,
    PublicProspectRequestClaim,
    PublicProspectRegistrationResult,
    VolunteerAlreadyExistsError as VolunteerAlreadyExistsError,
    VolunteerApplicationConflictError,
    VolunteerApplicationDetail,
    VolunteerApplicationFieldConflictError,
    VolunteerApplicationFieldValidationError,
    VolunteerApplicationInvite,
    VolunteerApplicationNotFoundError,
    VolunteerApplicationValidationError,
    VolunteerApplicationsRepositoryProtocol,
    VolunteerCreatorProtocol,
    VolunteerApplicationSubmissionInput,
    build_full_name as _build_full_name,
)
from app.domain.volunteer_applications.queries import VolunteerApplicationsQueries
from app.domain.volunteer_applications.workflow import VolunteerApplicationWorkflow
from app.email_delivery import EmailDeliveryOutboxProtocol
from app.observability import current_trace_id

_REGISTRATION_NOT_FOUND = "Registration was not found."
_CHANGED_CONCURRENTLY = (
    "The application was changed by someone else. Reload and try again."
)

_PUBLIC_PROSPECT_ROLE_ROUTES = {
    "grondahls": ("skjenke-gruppen", "Pubdyr", "Grøndahls"),
    "halvtimen": ("skjenke-gruppen", "Halvtimen-skjenker", "Halvtimen"),
    "kokkegruppen": ("skjenke-gruppen", "Kokk", "Kokkegruppen"),
    "stjernebarn": ("skjenke-gruppen", "Stjernebarn", "Stjernebarn"),
    "stjernesalen": ("skjenke-gruppen", "Stjernebarn", "Stjernesalen"),
    "quiz-gruppen": ("quiz", None, "Quiz-gruppen"),
}


def _normalize_phone(phone: str) -> str:
    try:
        return normalize_required_phone_number(phone)
    except ValueError as exc:
        raise VolunteerApplicationValidationError(str(exc)) from exc


def _check_postal_code(postal_code: str | None) -> None:
    if postal_code is None or not re.fullmatch(r"\d{4}", postal_code):
        raise VolunteerApplicationValidationError(
            "Postal code must be exactly 4 digits (e.g. 5011)."
        )


def _validate_required_profile_fields(
    submission: VolunteerApplicationSubmissionInput,
) -> None:
    if submission.birth_date is None:
        raise VolunteerApplicationValidationError("Birth date is required.")
    if not submission.address or not submission.address.strip():
        raise VolunteerApplicationValidationError("Address is required.")
    submission.address = submission.address.strip()
    submission.postal_code = (
        submission.postal_code.strip() if submission.postal_code else None
    )
    _check_postal_code(submission.postal_code)


logger = logging.getLogger(__name__)


class VolunteerApplicationsService(VolunteerApplicationsQueries):
    def __init__(
        self,
        settings: Settings,
        repository: VolunteerApplicationsRepositoryProtocol,
        volunteer_creator: VolunteerCreatorProtocol,
        email_outbox: EmailDeliveryOutboxProtocol,
        storage_service: StorageProtocol | None = None,
        photo_processor: PhotoProcessorProtocol | None = None,
        pending_count_cache_ttl_seconds: int = 30,
    ) -> None:
        super().__init__(
            repository=repository,
            pending_count_cache_ttl_seconds=pending_count_cache_ttl_seconds,
        )
        self.settings = settings
        self.volunteer_creator = volunteer_creator
        self.storage_service = storage_service
        self.photo_processor = photo_processor
        self.workflow = VolunteerApplicationWorkflow(
            operations=self,
            side_effects=VolunteerApplicationSideEffects(
                invalidate_pending_count_cache=self._invalidate_pending_count_cache,
                storage_service=storage_service,
            ),
            email_outbox=email_outbox,
        )

    async def create_public_prospect_registration(
        self,
        registration: PublicProspectRegistrationInput,
        *,
        base_url: str | None = None,
        idempotency_key: UUID | None = None,
        request_hash: str | None = None,
    ) -> VolunteerApplicationDetail:
        return await self.workflow.register_public_prospect(
            registration,
            base_url=base_url,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )

    def _require_repository(self) -> VolunteerApplicationsRepositoryProtocol:
        if self.repository is None:
            raise NotConfiguredError(
                "Volunteer applications require a configured repository."
            )
        return self.repository

    async def claim_public_prospect_request(
        self,
        *,
        idempotency_key: UUID,
        request_hash: str,
    ) -> PublicProspectRequestClaim:
        return await self.repository.claim_public_prospect_request(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            now=datetime.now(UTC),
        )

    async def complete_public_prospect_request(
        self,
        *,
        request_hash: str,
        registration_id: int,
        registration_ids: tuple[int, ...] = (),
    ) -> None:
        await self.repository.complete_public_prospect_request(
            request_hash=request_hash,
            registration_id=registration_id,
            registration_ids=registration_ids,
            now=datetime.now(UTC),
        )

    async def create_public_prospect_registration_record(
        self,
        registration: PublicProspectRegistrationInput,
        *,
        base_url: str | None = None,
    ) -> PublicProspectRegistrationResult:
        repository = self._require_repository()
        normalized_email = registration.email.strip().lower()
        if not normalized_email:
            raise VolunteerApplicationValidationError("E-postadresse er påkrevd.")
        friend_emails = self._normalize_friend_emails(
            registration.friend_emails,
            inviter_email=normalized_email,
        )
        await repository.lock_registration_emails([normalized_email, *friend_emails])
        first_choice_slug = registration.first_choice_group_slug.strip().lower()
        second_choice_slug = (
            (registration.second_choice_group_slug or "").strip().lower()
        )
        if not first_choice_slug:
            raise VolunteerApplicationValidationError("Velg et førstevalg.")
        if second_choice_slug and first_choice_slug == second_choice_slug:
            raise VolunteerApplicationValidationError(
                "Førstevalg og andrevalg må være ulike grupper."
            )

        choice_slugs = [
            slug for slug in [first_choice_slug, second_choice_slug] if slug
        ]
        resolved_choice_slugs = [
            _PUBLIC_PROSPECT_ROLE_ROUTES.get(slug, (slug, None, None))[0]
            for slug in choice_slugs
        ]
        groups_by_slug = await repository.find_public_prospect_groups_by_slugs(
            list(dict.fromkeys(resolved_choice_slugs))
        )
        if any(slug not in groups_by_slug for slug in resolved_choice_slugs):
            raise VolunteerApplicationValidationError(
                "Én eller flere valgte grupper finnes ikke."
            )

        # Resolve every target before creating anything, so the batch is atomic.
        targets = []
        for slug, resolved_slug in zip(
            choice_slugs, resolved_choice_slugs, strict=True
        ):
            group = groups_by_slug[resolved_slug]
            route = _PUBLIC_PROSPECT_ROLE_ROUTES.get(slug)
            role_id = None
            if route is not None and route[1] is not None:
                role_id = await repository.find_public_prospect_role_id(
                    group_id=group.group_id,
                    role_name=route[1],
                )
                if role_id is None:
                    raise VolunteerApplicationValidationError(
                        "Den foreslåtte vervtypen finnes ikke."
                    )
            if await repository.has_target_membership(
                normalized_email,
                group_id=group.group_id,
                role_id=role_id,
                semester_code=get_current_semester_code(),
            ):
                raise VolunteerApplicationFieldConflictError(
                    "Du er allerede frivillig i en av de valgte gruppene. Kontakt gruppen hvis du ønsker å endre verv.",
                    {
                        "secondChoiceGroupSlug"
                        if targets
                        else "firstChoiceGroupSlug": "Du har allerede et aktivt verv her."
                    },
                    conflict_type="existing_target_membership",
                )
            if any(
                previous.group_id == group.group_id and previous_role == role_id
                for previous, previous_role, _ in targets
            ):
                raise VolunteerApplicationFieldValidationError(
                    "Velg ulike grupper eller verv.",
                    {
                        "secondChoiceGroupSlug": "Dette valget gjelder samme gruppe og verv."
                    },
                )
            targets.append((group, role_id, route[2] if route else group.name))

        first_name, last_name = _split_full_name(registration.full_name)
        results = []
        for group, role_id, label in targets:
            existing_id = await repository.find_active_registration_for_target(
                normalized_email,
                group_id=group.group_id,
                role_id=role_id,
            )
            if existing_id is not None:
                detail = await repository.get_volunteer_application_detail(existing_id)
                if detail is None:
                    raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
                # Do not return tokens or change the existing snapshot through the public API.
                results.append(
                    PublicProspectRegistrationResult(
                        detail=detail,
                        friend_invites=[],
                        created=False,
                    )
                )
                continue
            target_friends = [
                email
                for email in friend_emails
                if not await repository.has_target_membership(
                    email,
                    group_id=group.group_id,
                    role_id=role_id,
                    semester_code=get_current_semester_code(),
                )
            ]
            result = await repository.create_public_prospect_registration(
                token=token_urlsafe(24),
                email=normalized_email,
                first_name=first_name,
                last_name=last_name,
                phone=normalize_phone_number(registration.phone),
                study_institution=_normalize_optional_text(
                    registration.study_institution
                ),
                background_details=_normalize_optional_text(
                    registration.background_details
                ),
                first_choice_label=label,
                second_choice_label=None,
                initial_group_id=group.group_id,
                initial_role_id=role_id,
                first_choice_group_id=group.group_id,
                second_choice_group_id=None,
                friend_invites=[
                    (friend_email, token_urlsafe(24)) for friend_email in target_friends
                ],
                inviter_name=_build_full_name(first_name, last_name),
                origin_trace_id=current_trace_id(),
            )
            results.append(result)
        primary = results[0]
        primary.additional_results = results[1:]
        return primary

    async def create_volunteer_application_invitation(
        self,
        email: str,
        *,
        base_url: str | None = None,
        initial_group_id: int | None = None,
        initial_role_id: int | None = None,
        actor_user_account_id: int | None = None,
    ) -> VolunteerApplicationInvite:
        return await self.workflow.invite(
            email,
            base_url=base_url,
            initial_group_id=initial_group_id,
            initial_role_id=initial_role_id,
            actor_user_account_id=actor_user_account_id,
        )

    async def create_invitation_record(
        self,
        email: str,
        *,
        initial_group_id: int | None = None,
        initial_role_id: int | None = None,
    ) -> VolunteerApplicationInvite:
        repository = self._require_repository()
        normalized_email = email.strip().lower()
        if not normalized_email:
            raise VolunteerApplicationConflictError("An email address is required.")
        if (initial_group_id is None) != (initial_role_id is None):
            raise VolunteerApplicationConflictError(
                "Choose both group and assignment_roles, or leave both empty."
            )
        await repository.lock_registration_emails([normalized_email])
        duplicate_registration = await repository.find_active_registration_for_target(
            normalized_email,
            group_id=initial_group_id,
            role_id=initial_role_id,
        )
        if duplicate_registration is not None:
            return await self.resend_invitation_record(duplicate_registration)
        token = token_urlsafe(24)
        invite = await repository.create_volunteer_application_invitation(
            email=normalized_email,
            token=token,
            initial_group_id=initial_group_id,
            initial_role_id=initial_role_id,
        )
        return invite

    async def get_volunteer_application_detail(
        self, registration_id: int
    ) -> VolunteerApplicationDetail | None:
        return await self.repository.get_volunteer_application_detail(registration_id)

    async def get_volunteer_application_by_token(
        self, token: str
    ) -> VolunteerApplicationDetail | None:
        return await self.repository.get_volunteer_application_by_token(token)

    async def submit_volunteer_application(
        self,
        token: str,
        submission: VolunteerApplicationSubmissionInput,
        *,
        base_url: str | None = None,
        photo_filename: str | None = None,
        photo_content: bytes | None = None,
        photo_content_type: str | None = None,
    ) -> VolunteerApplicationDetail:
        try:
            return await self.workflow.submit(
                token,
                submission,
                base_url=base_url,
                photo_filename=photo_filename,
                photo_content=photo_content,
                photo_content_type=photo_content_type,
            )
        except IllegalTransition as exc:
            raise VolunteerApplicationConflictError(str(exc)) from exc

    async def submit_application_record(
        self,
        token: str,
        submission: VolunteerApplicationSubmissionInput,
        *,
        base_url: str | None = None,
        photo_filename: str | None = None,
        photo_content: bytes | None = None,
        photo_content_type: str | None = None,
    ) -> VolunteerApplicationDetail:
        existing = await self.get_volunteer_application_by_token(token)
        if existing is None:
            raise VolunteerApplicationNotFoundError("Registration token was not found.")

        submission.phone = _normalize_phone(submission.phone)
        _validate_required_profile_fields(submission)

        # --- Photo handling ---
        has_existing_photo = existing.photo_sha1 is not None
        has_new_photo = bool(photo_filename and photo_content)
        missing_required_photo = not has_existing_photo and not has_new_photo
        if missing_required_photo:
            raise VolunteerApplicationValidationError("Profilbilde er påkrevd.")

        photo_sha1 = existing.photo_sha1
        photo_filetype = existing.photo_filetype
        old_storage_path = _build_photo_storage_path(
            existing.photo_sha1, existing.photo_filetype
        )
        new_storage_path = old_storage_path
        uploaded_new_photo = False
        storage_service: StorageProtocol | None = None

        if has_new_photo:
            (
                photo_sha1,
                photo_filetype,
                new_storage_path,
                storage_service,
            ) = await self._upload_new_photo(photo_filename, photo_content)
            assert new_storage_path is not None
            uploaded_new_photo = True

        # --- Save and cleanup ---
        await self._save_and_cleanup_photos(
            existing=existing,
            submission=submission,
            photo_sha1=photo_sha1,
            photo_filetype=photo_filetype,
            old_storage_path=old_storage_path,
            new_storage_path=new_storage_path,
            uploaded_new_photo=uploaded_new_photo,
            storage_service=storage_service,
        )

        detail = await self.get_volunteer_application_by_token(token)
        if detail is None:
            raise VolunteerApplicationNotFoundError("Registration token was not found.")

        return detail

    async def _upload_new_photo(
        self, photo_filename: str, photo_content: bytes
    ) -> tuple[str, str, str, StorageProtocol]:
        safe_filename = _sanitize_filename(photo_filename)
        extension = _normalize_extension(safe_filename)
        if extension not in {"jpg", "jpeg", "png", "webp"}:
            raise VolunteerApplicationConflictError(
                "Photos must be jpg, jpeg, png, or webp."
            )
        storage_service = self._require_storage_service()
        photo_sha1 = token_hex(20)
        processed = self._require_photo_processor()(
            photo_content,
            max_upload_bytes=self.settings.photo_upload_max_bytes,
            max_dimension=self.settings.photo_max_dimension,
        )
        new_path = _build_photo_storage_path(photo_sha1, processed.extension)
        await to_thread(
            storage_service.upload_photo,
            new_path,
            processed.content,
            processed.content_type,
        )
        return photo_sha1, processed.extension, new_path, storage_service

    async def _save_and_cleanup_photos(
        self,
        *,
        existing: VolunteerApplicationDetail,
        submission: VolunteerApplicationSubmissionInput,
        photo_sha1: str | None,
        photo_filetype: str | None,
        old_storage_path: str | None,
        new_storage_path: str | None,
        uploaded_new_photo: bool,
        storage_service: StorageProtocol | None,
    ) -> None:
        try:
            await self.repository.save_submission(
                registration_id=existing.registration_id,
                email=existing.email,
                submission=submission,
                photo_sha1=photo_sha1,
                photo_filetype=photo_filetype,
            )
        except Exception:
            should_rollback = (
                uploaded_new_photo
                and storage_service is not None
                and new_storage_path is not None
                and new_storage_path != old_storage_path
            )
            if should_rollback:
                try:
                    await to_thread(storage_service.remove_photo, new_storage_path)
                except Exception:
                    pass
            raise

        should_remove_old = (
            uploaded_new_photo
            and old_storage_path is not None
            and old_storage_path != new_storage_path
        )
        if should_remove_old and not await self.repository.photo_in_use(
            existing.photo_sha1
        ):
            try:
                await to_thread(
                    self._require_storage_service().remove_photo,
                    old_storage_path,
                )
            except Exception:
                pass

    async def mark_contacted(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> VolunteerApplicationDetail:
        try:
            return await self.workflow.contact(
                registration_id,
                actor_user_account_id=actor_user_account_id,
            )
        except IllegalTransition as exc:
            raise VolunteerApplicationConflictError(str(exc)) from exc

    async def start_trial(
        self,
        registration_id: int,
        *,
        base_url: str | None = None,
        actor_user_account_id: int | None = None,
    ) -> VolunteerApplicationDetail:
        try:
            return await self.workflow.start_trial(
                registration_id,
                base_url=base_url,
                actor_user_account_id=actor_user_account_id,
            )
        except IllegalTransition as exc:
            raise VolunteerApplicationConflictError(str(exc)) from exc

    async def reject_volunteer_application(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> VolunteerApplicationDetail:
        try:
            return await self.workflow.reject(
                registration_id,
                actor_user_account_id=actor_user_account_id,
            )
        except IllegalTransition as exc:
            raise VolunteerApplicationConflictError(str(exc)) from exc

    async def reopen_volunteer_application(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> VolunteerApplicationDetail:
        try:
            return await self.workflow.reopen(
                registration_id,
                actor_user_account_id=actor_user_account_id,
            )
        except IllegalTransition as exc:
            raise VolunteerApplicationConflictError(str(exc)) from exc

    async def restore_volunteer_application(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> VolunteerApplicationDetail:
        detail = await self.get_volunteer_application_detail(registration_id)
        if detail is None:
            raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
        if detail.promoted_volunteer_id is None or detail.promoted_at is None:
            raise VolunteerApplicationConflictError(
                "Only previously promoted applications can be restored as volunteers."
            )
        if not detail.profile_complete:
            raise VolunteerApplicationConflictError(
                "The applicant must complete the full profile before promotion."
            )
        try:
            return await self.workflow.restore_volunteer(
                registration_id,
                actor_user_account_id=actor_user_account_id,
            )
        except IllegalTransition as exc:
            raise VolunteerApplicationConflictError(str(exc)) from exc

    async def set_application_status_record(
        self,
        registration_id: int,
        *,
        status: ApplicationState,
        expected_status: ApplicationState,
        start_trial: bool = False,
    ) -> VolunteerApplicationDetail:
        repository = self._require_repository()
        detail = await self.get_volunteer_application_detail(registration_id)
        if detail is None:
            raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
        await repository.lock_registration_emails([detail.email])
        detail = await self.get_volunteer_application_detail(registration_id)
        if detail is None:
            raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
        # The workflow validated the transition before the lock was held; a
        # concurrent request may have moved the application since then.
        if detail.status != expected_status:
            raise VolunteerApplicationConflictError(_CHANGED_CONCURRENTLY)
        if start_trial and detail.status != ApplicationState.CONTACTED:
            raise VolunteerApplicationConflictError(
                "Only contacted applications can start a trial."
            )
        if start_trial or (
            status == ApplicationState.CONTACTED
            and detail.status == ApplicationState.NOT_VOLUNTEER
        ):
            duplicate_registration = (
                await repository.find_active_registration_for_target(
                    detail.email,
                    group_id=detail.initial_group_id or detail.first_choice_group_id,
                    role_id=detail.initial_role_id,
                    exclude_id=registration_id,
                )
            )
            if duplicate_registration is not None:
                raise ActiveVolunteerRegistrationExistsError(
                    duplicate_registration, detail.email
                )
        if (
            status == ApplicationState.NOT_VOLUNTEER
            and detail.trial_assignment_id is not None
            and detail.promoted_volunteer_id is not None
        ):
            await self.volunteer_creator.remove_trial_assignment(
                volunteer_id=detail.promoted_volunteer_id,
                assignment_id=detail.trial_assignment_id,
            )
        trial_volunteer_id = detail.promoted_volunteer_id
        trial_assignment_id = detail.trial_assignment_id
        owns_profile = detail.owns_volunteer_profile
        if start_trial and trial_assignment_id is None:
            resolved_group_id = detail.initial_group_id or detail.first_choice_group_id
            trial_volunteer_id = (
                trial_volunteer_id
                or await repository.find_volunteer_id_by_email(detail.email)
            )
            if trial_volunteer_id is not None:
                (
                    trial_volunteer_id,
                    trial_assignment_id,
                ) = await self.volunteer_creator.add_application_assignment(
                    volunteer_id=trial_volunteer_id,
                    group_id=resolved_group_id,
                    role_id=detail.initial_role_id,
                    semester_code=get_current_semester_code(),
                    contract_signed=False,
                )
            else:
                owns_profile = True
                (
                    trial_volunteer_id,
                    trial_assignment_id,
                ) = await self.volunteer_creator.create_from_application(
                    first_name=detail.first_name,
                    last_name=detail.last_name or detail.email,
                    email=detail.email,
                    gender=detail.gender or "A",
                    birth_date=detail.birth_date,
                    street_address=detail.address,
                    postal_code=detail.postal_code,
                    phone=normalize_phone_number(detail.phone),
                    photo_sha1=detail.photo_sha1,
                    photo_filetype=detail.photo_filetype,
                    group_id=resolved_group_id,
                    role_id=detail.initial_role_id,
                    semester_code=get_current_semester_code(),
                    contract_signed=False,
                )
        updated = await repository.set_application_status(
            registration_id,
            status=status,
            start_trial=start_trial,
            volunteer_id=trial_volunteer_id if start_trial else None,
            trial_assignment_id=trial_assignment_id if start_trial else None,
            owns_volunteer_profile=owns_profile if start_trial else None,
            expected_status=expected_status,
        )
        if not updated:
            raise VolunteerApplicationConflictError(_CHANGED_CONCURRENTLY)
        refreshed = await self.get_volunteer_application_detail(registration_id)
        if refreshed is None:
            raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
        return refreshed

    async def approve_volunteer_application(
        self,
        registration_id: int,
        *,
        accepted_group_id: int | None = None,
        accepted_role_id: int | None = None,
        assignment_year: int | None = None,
        assignment_term: int | None = None,
        contract_signed: bool = True,
        base_url: str | None = None,
        actor_user_account_id: int | None = None,
    ) -> int:
        try:
            return await self.workflow.approve(
                registration_id,
                accepted_group_id=accepted_group_id,
                accepted_role_id=accepted_role_id,
                assignment_year=assignment_year,
                assignment_term=assignment_term,
                contract_signed=contract_signed,
                base_url=base_url,
                actor_user_account_id=actor_user_account_id,
            )
        except IllegalTransition as exc:
            raise VolunteerApplicationConflictError(str(exc)) from exc

    async def approve_application_record(
        self,
        registration_id: int,
        *,
        accepted_group_id: int | None = None,
        accepted_role_id: int | None = None,
        assignment_year: int | None = None,
        assignment_term: int | None = None,
        contract_signed: bool = True,
    ) -> tuple[VolunteerApplicationDetail, int]:
        repository = self._require_repository()
        detail = await self.get_volunteer_application_detail(registration_id)
        if detail is None:
            raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
        await repository.lock_registration_emails([detail.email])
        detail = await self.get_volunteer_application_detail(registration_id)
        if detail is None:
            raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
        if detail.status != ApplicationState.TRIAL:
            raise VolunteerApplicationConflictError(
                "Only trial applications can be promoted."
            )
        if not detail.profile_complete:
            raise VolunteerApplicationConflictError(
                "The applicant must complete the full profile before promotion."
            )
        resolved_group_id = (
            accepted_group_id or detail.initial_group_id or detail.first_choice_group_id
        )
        allowed_group_ids = {
            group_id
            for group_id in [
                detail.initial_group_id,
                detail.first_choice_group_id,
            ]
            if group_id is not None
        }
        if resolved_group_id is None:
            raise VolunteerApplicationConflictError(
                "Choose a group before promoting this prospect."
            )
        if allowed_group_ids and resolved_group_id not in allowed_group_ids:
            raise VolunteerApplicationConflictError(
                "The chosen group is not one of the registered committee choices."
            )
        resolved_role_id = accepted_role_id or detail.initial_role_id
        if resolved_role_id is None:
            raise VolunteerApplicationConflictError(
                "Choose a role before promoting this prospect."
            )
        if not (
            await repository.role_matches_group(
                role_id=resolved_role_id, group_id=resolved_group_id
            )
        ):
            raise VolunteerApplicationConflictError(
                "The selected initial assignment_roles is no longer valid for the chosen group."
            )
        if assignment_year is not None and not 1900 <= assignment_year <= 3000:
            raise VolunteerApplicationConflictError(
                "Year must be between 1900 and 3000."
            )
        if assignment_term is not None and assignment_term not in {1, 2}:
            raise VolunteerApplicationConflictError("Choose a valid semester.")
        if (assignment_year is None) != (assignment_term is None):
            raise VolunteerApplicationConflictError("Choose both year and semester.")
        semester_code = (
            assignment_year * 10 + assignment_term
            if assignment_year is not None and assignment_term is not None
            else get_current_semester_code()
        )
        # The volunteers module owns onboarding; this module only flips
        # its own invite row once the owning service reports the new id.
        linked_id = (
            detail.promoted_volunteer_id
            or await repository.find_volunteer_id_by_email(detail.email)
        )
        if linked_id is not None and not detail.owns_volunteer_profile:
            volunteer_id, _ = await self.volunteer_creator.add_application_assignment(
                volunteer_id=linked_id,
                group_id=resolved_group_id,
                role_id=resolved_role_id,
                semester_code=semester_code,
                contract_signed=contract_signed,
                assignment_id=detail.trial_assignment_id,
            )
        else:
            volunteer_id, _ = await self.volunteer_creator.create_from_application(
                first_name=detail.first_name,
                last_name=detail.last_name or "",
                email=detail.email,
                gender=detail.gender or "A",
                birth_date=detail.birth_date,
                street_address=detail.address,
                postal_code=detail.postal_code,
                phone=normalize_phone_number(detail.phone),
                photo_sha1=detail.photo_sha1,
                photo_filetype=detail.photo_filetype,
                group_id=resolved_group_id,
                role_id=resolved_role_id,
                semester_code=semester_code,
                contract_signed=contract_signed,
                volunteer_id=linked_id,
                assignment_id=detail.trial_assignment_id,
            )
        await repository.mark_promoted(
            registration_id=detail.registration_id,
            volunteer_id=volunteer_id,
            accepted_group_id=resolved_group_id,
        )
        return detail, volunteer_id

    async def delete_volunteer_application(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> None:
        try:
            await self.workflow.delete(
                registration_id, actor_user_account_id=actor_user_account_id
            )
        except IllegalTransition as exc:
            raise VolunteerApplicationConflictError(str(exc)) from exc

    async def delete_application_record(
        self, registration_id: int
    ) -> VolunteerApplicationDetail:
        detail = await self.get_volunteer_application_detail(registration_id)
        if detail is None:
            raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
        repository = self._require_repository()
        await repository.lock_registration_emails([detail.email])
        detail = await self.get_volunteer_application_detail(registration_id)
        if detail is None:
            raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
        if detail.status != ApplicationState.NEW:
            raise VolunteerApplicationConflictError(_CHANGED_CONCURRENTLY)
        if detail.pending_volunteer_id is not None:
            raise VolunteerApplicationConflictError(
                "Submitted applications must be rejected instead of deleted."
            )
        await self.repository.delete_volunteer_application(registration_id)
        return detail

    async def find_active_trial_applicant_by_email(
        self, email: str, *, include_expired: bool = False
    ):
        return await self.repository.find_active_trial_applicant_by_email(
            email, include_expired=include_expired
        )

    async def get_active_trial_applicant(
        self, application_id: int, *, include_expired: bool = False
    ):
        return await self.repository.get_active_trial_applicant(
            application_id, include_expired=include_expired
        )

    async def get_approved_volunteer_id(self, application_id: int) -> int | None:
        return await self._require_repository().get_approved_volunteer_id(
            application_id
        )

    async def resend_volunteer_application_invitation(
        self,
        registration_id: int,
        *,
        base_url: str | None = None,
        actor_user_account_id: int | None = None,
    ) -> VolunteerApplicationDetail:
        try:
            return await self.workflow.resend_invitation(
                registration_id,
                base_url=base_url,
                actor_user_account_id=actor_user_account_id,
            )
        except IllegalTransition as exc:
            raise VolunteerApplicationConflictError(str(exc)) from exc

    async def resend_invitation_record(
        self,
        registration_id: int,
    ) -> VolunteerApplicationDetail:
        detail = await self.get_volunteer_application_detail(registration_id)
        if detail is None:
            raise VolunteerApplicationNotFoundError(_REGISTRATION_NOT_FOUND)
        return detail

    async def append_domain_event(
        self, event: DomainEventRecord, *, subject_id: int
    ) -> int:
        return await self.repository.append_domain_event(event, subject_id=subject_id)

    def _normalize_friend_emails(
        self,
        friend_emails: list[str] | None,
        *,
        inviter_email: str,
    ) -> list[str]:
        normalized = [
            email.strip().lower()
            for email in friend_emails or []
            if email and email.strip()
        ]
        if len(normalized) > 2:
            raise VolunteerApplicationFieldValidationError(
                "Du kan legge til maks to venner.",
                {"friendEmails": "Du kan legge til maks to venner."},
            )
        seen: set[str] = {inviter_email}
        field_errors: dict[str, str] = {}
        for index, email in enumerate(normalized):
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                field_errors[str(index)] = "Skriv inn en gyldig e-postadresse."
            elif email in seen:
                field_errors[str(index)] = "E-postadressene må være ulike."
            seen.add(email)
        if field_errors:
            raise VolunteerApplicationFieldValidationError(
                "Én eller flere venneadresser er ugyldige.",
                {"friendEmails": field_errors},
            )
        return normalized

    def _require_photo_processor(self) -> PhotoProcessorProtocol:
        if self.photo_processor is None:
            raise NotConfiguredError("Photo processing is not configured yet.")
        return self.photo_processor

    def _require_storage_service(self) -> StorageProtocol:
        if self.storage_service is None:
            raise NotConfiguredError(
                "Supabase credentials are required for storage integration."
            )
        return self.storage_service


def _split_full_name(full_name: str) -> tuple[str | None, str]:
    parts = [part for part in full_name.strip().split() if part]
    if not parts:
        raise VolunteerApplicationValidationError("Navn er påkrevd.")
    if len(parts) == 1:
        return None, parts[0]
    return " ".join(parts[:-1]), parts[-1]


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _sanitize_filename(filename: str) -> str:
    stripped = filename.strip()
    if not stripped:
        return "photo"
    return stripped.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _normalize_extension(filename: str) -> str:
    if "." not in filename:
        raise VolunteerApplicationConflictError("Photos must include a file extension.")
    return filename.rsplit(".", 1)[-1].lower()


def _resolve_content_type(filename: str, content_type: str | None) -> str:
    if content_type:
        return content_type
    extension = _normalize_extension(filename)
    if extension in {"jpg", "jpeg"}:
        return "image/jpeg"
    if extension == "png":
        return "image/png"
    if extension == "webp":
        return "image/webp"
    return "application/octet-stream"


def _build_photo_storage_path(
    filename_hash: str | None, extension: str | None
) -> str | None:
    if not filename_hash or not extension:
        return None
    return f"{filename_hash}.{extension}"
