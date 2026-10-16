"""Workflow coordinator for the volunteer application lifecycle.

Every lifecycle action follows the same shape:

1. load the typed record,
2. ask the pure state machine whether the transition is legal
   (``IllegalTransition`` propagates to the service layer, which maps
   it onto ``VolunteerApplicationConflictError``),
3. persist the change through the operations protocol,
4. append the domain event row in the same request transaction,
5. enqueue durable email work in the same transaction,
6. commit the unit of work,
7. execute non-email side effects and request an immediate best-effort dispatch.

The dispatcher runs only after commit, so no email may announce a state
the database can still roll back. Each application is approved and enqueued
independently, so one applicant's lifecycle never controls another's.
"""

from __future__ import annotations

from app.observability import get_logger

from dataclasses import replace
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from opentelemetry import trace

from app.db.session import commit_request_session, rollback_request_session
from app.email_delivery import (
    APPLICANT_APPLICATION_RECEIVED,
    APPLICANT_FRIEND_INVITATION,
    APPLICANT_INVITATION,
    APPLICANT_PROFILE_COMPLETION,
    EmailDeliveryOutboxProtocol,
    EmailDeliveryRequest,
)
from app.domain.volunteer_applications.models import (
    VolunteerApplicationNotFoundError,
)
from app.domain.volunteer_applications.state_machine import (
    ApplicationAction,
    ApplicationState,
    DomainEventRecord,
    TransitionContext,
    application_transition,
)
from app.observability import current_trace_id, with_named_span

if TYPE_CHECKING:
    from app.domain.volunteer_applications.service import (
        PublicProspectRegistrationInput,
        PublicProspectRegistrationResult,
        VolunteerApplicationDetail,
        VolunteerApplicationInvite,
        VolunteerApplicationSubmissionInput,
    )


class VolunteerApplicationWorkflowOperations(Protocol):
    async def claim_public_prospect_request(
        self,
        *,
        idempotency_key: UUID,
        request_hash: str,
    ): ...
    async def complete_public_prospect_request(
        self,
        *,
        request_hash: str,
        registration_id: int,
        registration_ids: tuple[int, ...] = (),
    ) -> None: ...
    async def create_public_prospect_registration_record(
        self,
        registration: "PublicProspectRegistrationInput",
        *,
        base_url: str | None,
    ) -> "PublicProspectRegistrationResult": ...
    async def create_invitation_record(
        self,
        email: str,
        *,
        initial_group_id: int | None,
        initial_role_id: int | None,
    ) -> "VolunteerApplicationInvite": ...
    async def submit_application_record(
        self,
        token: str,
        submission: "VolunteerApplicationSubmissionInput",
        *,
        base_url: str | None,
        photo_filename: str | None,
        photo_content: bytes | None,
        photo_content_type: str | None,
    ) -> "VolunteerApplicationDetail": ...
    async def set_application_status_record(
        self,
        registration_id: int,
        *,
        status: ApplicationState,
        expected_status: ApplicationState,
        start_trial: bool = False,
    ) -> "VolunteerApplicationDetail": ...
    async def approve_application_record(
        self,
        registration_id: int,
        *,
        accepted_group_id: int | None,
        accepted_role_id: int | None = None,
        assignment_year: int | None = None,
        assignment_term: int | None = None,
        contract_signed: bool = True,
    ) -> "tuple[VolunteerApplicationDetail, int]": ...
    async def delete_application_record(
        self, registration_id: int
    ) -> "VolunteerApplicationDetail": ...
    async def resend_invitation_record(
        self, registration_id: int
    ) -> "VolunteerApplicationInvite": ...
    async def get_volunteer_application_detail(
        self, registration_id: int
    ) -> "VolunteerApplicationDetail | None": ...
    async def get_volunteer_application_by_token(
        self, token: str
    ) -> "VolunteerApplicationDetail | None": ...
    async def append_domain_event(
        self, event: DomainEventRecord, *, subject_id: int
    ) -> int: ...


class VolunteerApplicationSideEffectsProtocol(Protocol):
    async def after_public_prospect_registered(
        self, result: "PublicProspectRegistrationResult", *, base_url: str | None
    ) -> None: ...
    async def after_invited(
        self, invite: "VolunteerApplicationInvite", *, base_url: str | None
    ) -> None: ...
    async def after_submitted(
        self,
        detail: "VolunteerApplicationDetail",
    ) -> None: ...
    async def after_trial_started(
        self,
        detail: "VolunteerApplicationDetail",
        *,
        base_url: str | None,
    ) -> None: ...
    async def after_approved(
        self,
        detail: "VolunteerApplicationDetail",
        *,
        volunteer_id: int,
        base_url: str | None,
    ) -> None: ...
    async def after_deleted(
        self,
        detail: "VolunteerApplicationDetail",
    ) -> None: ...
    async def after_invitation_resent(
        self, detail: "VolunteerApplicationInvite", *, base_url: str | None
    ) -> None: ...


def _approval_context(
    detail: "VolunteerApplicationDetail",
    *,
    actor_user_account_id: int | None,
) -> TransitionContext:
    return TransitionContext(
        actor_user_account_id=actor_user_account_id,
        has_complete_profile=detail.profile_complete,
    )


class VolunteerApplicationWorkflow:
    def __init__(
        self,
        *,
        operations: VolunteerApplicationWorkflowOperations,
        side_effects: VolunteerApplicationSideEffectsProtocol,
        email_outbox: EmailDeliveryOutboxProtocol,
    ) -> None:
        self.operations = operations
        self.side_effects = side_effects
        self.email_outbox = email_outbox

    async def invite(
        self,
        email: str,
        *,
        base_url: str | None,
        initial_group_id: int | None,
        initial_role_id: int | None,
        actor_user_account_id: int | None = None,
    ) -> "VolunteerApplicationInvite":
        with with_named_span("volunteer.application.invite"):
            invite = await self.operations.create_invitation_record(
                email,
                initial_group_id=initial_group_id,
                initial_role_id=initial_role_id,
            )
            # Creation is not a transition; the audit row is appended directly.
            event_id = await self.operations.append_domain_event(
                DomainEventRecord(
                    event_type="application_invited",
                    actor_user_account_id=actor_user_account_id,
                    subject_type="application",
                    subject_id=invite.registration_id,
                    payload={"email": invite.email},
                ),
                subject_id=invite.registration_id,
            )
            await self._enqueue_volunteer_email(
                template_key=APPLICANT_INVITATION,
                invite=invite,
                source_domain_event_id=event_id,
            )
            await commit_request_session()
            self._record_lifecycle(invite, status="application_invited")
            await self.side_effects.after_invited(invite, base_url=base_url)
            await self._dispatch_best_effort()
            return invite

    async def register_public_prospect(
        self,
        registration: "PublicProspectRegistrationInput",
        *,
        base_url: str | None,
        idempotency_key: UUID | None = None,
        request_hash: str | None = None,
    ) -> "VolunteerApplicationDetail":
        if (idempotency_key is None) != (request_hash is None):
            raise ValueError(
                "Volunteer prospect idempotency key and request hash must be provided together."
            )
        try:
            if idempotency_key is not None and request_hash is not None:
                claim = await self.operations.claim_public_prospect_request(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if not claim.created:
                    if claim.registration_id is None:
                        raise RuntimeError(
                            "Completed volunteer prospect claim has no registration."
                        )
                    detail = await self.operations.get_volunteer_application_detail(
                        claim.registration_id
                    )
                    if detail is None:
                        raise RuntimeError(
                            "Idempotent volunteer prospect result no longer exists."
                        )
                    detail.registration_ids = claim.registration_ids or (
                        detail.registration_id,
                    )
                    await commit_request_session()
                    return detail

            result = await self.operations.create_public_prospect_registration_record(
                registration, base_url=base_url
            )
            applications = [result, *result.additional_results]
            result.detail.registration_ids = tuple(
                item.detail.registration_id for item in applications
            )
            for application in applications:
                registration_event_id = await self.operations.append_domain_event(
                    DomainEventRecord(
                        event_type="prospect_registered"
                        if application.created
                        else "application_invitation_resent",
                        actor_user_account_id=None,
                        subject_type="application",
                        subject_id=application.detail.registration_id,
                        payload={
                            "email": application.detail.email,
                            "friend_invites": [
                                invite.registration_id
                                for invite in application.friend_invites
                            ],
                        },
                    ),
                    subject_id=application.detail.registration_id,
                )
                await self._enqueue_volunteer_email(
                    template_key=APPLICANT_APPLICATION_RECEIVED
                    if application.created
                    else APPLICANT_INVITATION,
                    invite=application.detail,
                    source_domain_event_id=registration_event_id,
                )
                for invite in application.friend_invites:
                    event_id = await self.operations.append_domain_event(
                        DomainEventRecord(
                            event_type="application_invited",
                            actor_user_account_id=None,
                            subject_type="application",
                            subject_id=invite.registration_id,
                            payload={
                                "email": invite.email,
                                "invited_by_registration_id": application.detail.registration_id,
                                "inviter_name": invite.inviter_name,
                                "inviter_email": application.detail.email,
                                "invitee_email": invite.email,
                            },
                        ),
                        subject_id=invite.registration_id,
                    )
                    await self._enqueue_volunteer_email(
                        template_key=APPLICANT_FRIEND_INVITATION,
                        invite=invite,
                        source_domain_event_id=event_id,
                    )
            if request_hash is not None:
                await self.operations.complete_public_prospect_request(
                    request_hash=request_hash,
                    registration_id=result.detail.registration_id,
                    registration_ids=result.detail.registration_ids,
                )
            await commit_request_session()
        except Exception:
            await rollback_request_session()
            raise
        for application in applications:
            self._record_lifecycle(
                application.detail,
                status=(
                    "prospect_registered"
                    if application.created
                    else "application_invitation_resent"
                ),
            )
        await self.side_effects.after_public_prospect_registered(
            result, base_url=base_url
        )
        await self._dispatch_best_effort()
        return result.detail

    async def submit(
        self,
        token: str,
        submission: "VolunteerApplicationSubmissionInput",
        *,
        base_url: str | None,
        photo_filename: str | None,
        photo_content: bytes | None,
        photo_content_type: str | None,
    ) -> "VolunteerApplicationDetail":
        with with_named_span("volunteer.application.submit"):
            existing = await self.operations.get_volunteer_application_by_token(token)
            result = None
            if existing is not None:
                result = application_transition(
                    ApplicationState(existing.status),
                    ApplicationAction.SUBMIT_PROFILE,
                )
            detail = await self.operations.submit_application_record(
                token,
                submission,
                base_url=base_url,
                photo_filename=photo_filename,
                photo_content=photo_content,
                photo_content_type=photo_content_type,
            )
            if result is not None and result.event is not None:
                await self.operations.append_domain_event(
                    replace(result.event, subject_id=detail.registration_id),
                    subject_id=detail.registration_id,
                )
            await commit_request_session()
            self._record_lifecycle(detail, status="application_submitted")
            await self.side_effects.after_submitted(detail)
            return detail

    async def contact(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> "VolunteerApplicationDetail":
        return await self._change_status(
            registration_id,
            action=ApplicationAction.CONTACT,
            actor_user_account_id=actor_user_account_id,
        )

    async def start_trial(
        self,
        registration_id: int,
        *,
        base_url: str | None,
        actor_user_account_id: int | None = None,
    ) -> "VolunteerApplicationDetail":
        detail = await self._change_status(
            registration_id,
            action=ApplicationAction.START_TRIAL,
            actor_user_account_id=actor_user_account_id,
            start_trial=True,
            email_template_key=APPLICANT_PROFILE_COMPLETION,
        )
        await self.side_effects.after_trial_started(detail, base_url=base_url)
        return detail

    async def reject(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> "VolunteerApplicationDetail":
        return await self._change_status(
            registration_id,
            action=ApplicationAction.REJECT,
            actor_user_account_id=actor_user_account_id,
        )

    async def reopen(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> "VolunteerApplicationDetail":
        return await self._change_status(
            registration_id,
            action=ApplicationAction.REOPEN,
            actor_user_account_id=actor_user_account_id,
        )

    async def restore_volunteer(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> "VolunteerApplicationDetail":
        return await self._change_status(
            registration_id,
            action=ApplicationAction.RESTORE_VOLUNTEER,
            actor_user_account_id=actor_user_account_id,
        )

    async def _change_status(
        self,
        registration_id: int,
        *,
        action: ApplicationAction,
        actor_user_account_id: int | None,
        start_trial: bool = False,
        email_template_key: str | None = None,
    ) -> "VolunteerApplicationDetail":
        existing = await self.operations.get_volunteer_application_detail(
            registration_id
        )
        if existing is None:
            raise VolunteerApplicationNotFoundError("Registration was not found.")
        current_state = ApplicationState(existing.status)
        result = application_transition(
            current_state,
            action,
            context=TransitionContext(actor_user_account_id=actor_user_account_id),
        )
        detail = await self.operations.set_application_status_record(
            registration_id,
            status=result.new_state,
            expected_status=current_state,
            start_trial=start_trial,
        )
        event_id = None
        if result.event is not None:
            event_id = await self.operations.append_domain_event(
                replace(
                    result.event,
                    payload={
                        **result.event.payload,
                        "new_state": result.new_state.value,
                    },
                ),
                subject_id=registration_id,
            )
        if email_template_key is not None:
            if event_id is None:
                raise RuntimeError("Email-producing domain event was not persisted.")
            await self._enqueue_volunteer_email(
                template_key=email_template_key,
                invite=detail,
                source_domain_event_id=event_id,
            )
        await commit_request_session()
        if result.event is not None:
            self._record_lifecycle(detail, status=result.event.event_type)
        if email_template_key is not None:
            await self._dispatch_best_effort()
        return detail

    async def approve(
        self,
        registration_id: int,
        *,
        accepted_group_id: int | None,
        accepted_role_id: int | None = None,
        assignment_year: int | None = None,
        assignment_term: int | None = None,
        contract_signed: bool = True,
        base_url: str | None,
        actor_user_account_id: int | None = None,
    ) -> int:
        with with_named_span("volunteer.application.approve"):
            detail, volunteer_id, _ = await self._approve_one(
                registration_id,
                accepted_group_id=accepted_group_id,
                accepted_role_id=accepted_role_id,
                assignment_year=assignment_year,
                assignment_term=assignment_term,
                contract_signed=contract_signed,
                actor_user_account_id=actor_user_account_id,
            )
            await commit_request_session()
            self._record_lifecycle(
                detail, status="application_approved", volunteer_id=volunteer_id
            )
            await self.side_effects.after_approved(
                detail, volunteer_id=volunteer_id, base_url=base_url
            )
            return volunteer_id

    async def _approve_one(
        self,
        registration_id: int,
        *,
        accepted_group_id: int | None,
        accepted_role_id: int | None = None,
        assignment_year: int | None = None,
        assignment_term: int | None = None,
        contract_signed: bool = True,
        actor_user_account_id: int | None,
    ) -> "tuple[VolunteerApplicationDetail, int, DomainEventRecord | None]":
        existing = await self.operations.get_volunteer_application_detail(
            registration_id
        )
        result = None
        if existing is not None:
            result = application_transition(
                ApplicationState(existing.status),
                ApplicationAction.PROMOTE,
                context=_approval_context(
                    existing,
                    actor_user_account_id=actor_user_account_id,
                ),
            )
        detail, volunteer_id = await self.operations.approve_application_record(
            registration_id,
            accepted_group_id=accepted_group_id,
            accepted_role_id=accepted_role_id,
            assignment_year=assignment_year,
            assignment_term=assignment_term,
            contract_signed=contract_signed,
        )
        event = None
        if result is not None and result.event is not None:
            event = replace(
                result.event,
                subject_id=registration_id,
                payload={
                    **result.event.payload,
                    "volunteer_id": volunteer_id,
                },
            )
            event_id = await self.operations.append_domain_event(
                event, subject_id=registration_id
            )
            event = replace(event, event_id=event_id)
        return detail, volunteer_id, event

    async def delete(
        self,
        registration_id: int,
        *,
        actor_user_account_id: int | None = None,
    ) -> None:
        with with_named_span("volunteer.application.delete"):
            existing = await self.operations.get_volunteer_application_detail(
                registration_id
            )
            result = None
            if existing is not None:
                result = application_transition(
                    ApplicationState(existing.status),
                    ApplicationAction.DELETE,
                    context=TransitionContext(
                        actor_user_account_id=actor_user_account_id
                    ),
                )
            detail = await self.operations.delete_application_record(registration_id)
            if result is not None and result.event is not None:
                await self.operations.append_domain_event(
                    replace(
                        result.event,
                        subject_id=registration_id,
                        payload={
                            **result.event.payload,
                            "email": detail.email,
                        },
                    ),
                    subject_id=registration_id,
                )
            await commit_request_session()
            self._record_lifecycle(detail, status="application_deleted")
            await self.side_effects.after_deleted(detail)

    async def resend_invitation(
        self,
        registration_id: int,
        *,
        base_url: str | None,
        actor_user_account_id: int | None = None,
    ) -> "VolunteerApplicationInvite":
        with with_named_span("volunteer.application.resend_invitation"):
            existing = await self.operations.get_volunteer_application_detail(
                registration_id
            )
            result = None
            if existing is not None:
                result = application_transition(
                    ApplicationState(existing.status),
                    ApplicationAction.RESEND_INVITATION,
                    context=TransitionContext(
                        actor_user_account_id=actor_user_account_id
                    ),
                )
            detail = await self.operations.resend_invitation_record(registration_id)
            if result is not None and result.event is not None:
                event_id = await self.operations.append_domain_event(
                    replace(result.event, subject_id=registration_id),
                    subject_id=registration_id,
                )
                await self._enqueue_volunteer_email(
                    template_key=APPLICANT_INVITATION,
                    invite=detail,
                    source_domain_event_id=event_id,
                )
            await commit_request_session()
            self._record_lifecycle(detail, status="invitation_resent")
            await self.side_effects.after_invitation_resent(detail, base_url=base_url)
            await self._dispatch_best_effort()
            return detail

    async def _enqueue_volunteer_email(
        self,
        *,
        template_key: str,
        invite: object,
        source_domain_event_id: int,
    ) -> None:
        registration_id = int(getattr(invite, "registration_id"))
        await self.email_outbox.enqueue(
            EmailDeliveryRequest(
                template_key=template_key,
                template_version=1,
                recipient_email=str(getattr(invite, "email")),
                business_type="volunteer_application",
                business_id=str(registration_id),
                idempotency_key=(
                    f"domain-event:{source_domain_event_id}:{template_key}:applicant"
                ),
                registration_id=registration_id,
                source_domain_event_id=source_domain_event_id,
                enqueued_trace_id=current_trace_id(),
            )
        )

    async def _dispatch_best_effort(self) -> None:
        try:
            await self.email_outbox.dispatch_due(batch_size=10)
        except Exception:
            get_logger(__name__).error(
                "email.delivery",
                extra={
                    "outcome": "dispatch_deferred",
                    "error_category": "unexpected",
                },
            )

    def _record_lifecycle(
        self,
        detail: object,
        *,
        status: str,
        volunteer_id: int | None = None,
    ) -> None:
        registration_id = int(getattr(detail, "registration_id"))
        origin_trace_id = getattr(detail, "origin_trace_id", None)
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("registration_id", registration_id)
            if origin_trace_id:
                span.set_attribute("origin_trace_id", origin_trace_id)
            if volunteer_id is not None:
                span.set_attribute("volunteer_id", volunteer_id)
        get_logger(__name__).info(
            "volunteer.lifecycle",
            extra={
                "registration_id": registration_id,
                "origin_trace_id": origin_trace_id,
                "volunteer_id": volunteer_id,
                "status": status,
            },
        )
