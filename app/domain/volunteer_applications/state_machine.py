"""Pure, I/O-free volunteer application state machine.

Encodes the application lifecycle as explicit states, actions, and
transitions.  Every state/action pair is either legal (produces a
``TransitionResult``) or illegal (raises ``IllegalTransition``).

The application status is deliberately independent of whether the applicant
has completed every profile field. The five user-facing states are::

    new ──contact─────────────────────► contacted
    contacted ──start-trial───────────► trial
    trial ──promote───────────────────► volunteer
    {new,contacted,trial,volunteer} ──reject──► not_volunteer
    not_volunteer ──reopen────────────► contacted

``SUBMIT_PROFILE`` keeps the current lifecycle state. This lets a candidate
complete the profile after the trial has started without moving backwards.

See ``docs/adr/003-domain-event-log.md`` for the audit design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


# ── Enums ──────────────────────────────────────────────────────────


class ApplicationState(StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    TRIAL = "trial"
    VOLUNTEER = "volunteer"
    NOT_VOLUNTEER = "not_volunteer"


class ApplicationAction(StrEnum):
    SUBMIT_PROFILE = "submit_profile"
    RESEND_INVITATION = "resend_invitation"
    CONTACT = "contact"
    START_TRIAL = "start_trial"
    PROMOTE = "promote"
    REJECT = "reject"
    REOPEN = "reopen"
    RESTORE_VOLUNTEER = "restore_volunteer"
    DELETE = "delete"


# ── Errors ─────────────────────────────────────────────────────────


class IllegalTransition(ValueError):
    """Raised when a transition is not allowed for the given state."""


# ── Transition table ───────────────────────────────────────────────

# Maps (current_state, action) → new_state.
# Only legal transitions are listed; anything else raises IllegalTransition.
_TRANSITIONS: dict[
    tuple[ApplicationState, ApplicationAction], ApplicationState
] = {
    **{
        (state, ApplicationAction.SUBMIT_PROFILE): state
        for state in (
            ApplicationState.NEW,
            ApplicationState.CONTACTED,
            ApplicationState.TRIAL,
            ApplicationState.VOLUNTEER,
        )
    },
    (ApplicationState.NEW, ApplicationAction.RESEND_INVITATION): ApplicationState.NEW,
    (ApplicationState.CONTACTED, ApplicationAction.RESEND_INVITATION): ApplicationState.CONTACTED,
    (ApplicationState.NEW, ApplicationAction.CONTACT): ApplicationState.CONTACTED,
    (ApplicationState.CONTACTED, ApplicationAction.START_TRIAL): ApplicationState.TRIAL,
    (ApplicationState.TRIAL, ApplicationAction.PROMOTE): ApplicationState.VOLUNTEER,
    (ApplicationState.NEW, ApplicationAction.REJECT): ApplicationState.NOT_VOLUNTEER,
    (ApplicationState.CONTACTED, ApplicationAction.REJECT): ApplicationState.NOT_VOLUNTEER,
    (ApplicationState.TRIAL, ApplicationAction.REJECT): ApplicationState.NOT_VOLUNTEER,
    (ApplicationState.VOLUNTEER, ApplicationAction.REJECT): ApplicationState.NOT_VOLUNTEER,
    (ApplicationState.NOT_VOLUNTEER, ApplicationAction.REOPEN): ApplicationState.CONTACTED,
    (ApplicationState.NOT_VOLUNTEER, ApplicationAction.RESTORE_VOLUNTEER): ApplicationState.VOLUNTEER,
}

# Actions that result in row removal (no target state).
_DELETE_ACTIONS = {ApplicationAction.DELETE}

# Audit event emitted for each action. SUBMIT_PROFILE depends on the state
# and is resolved in ``application_transition``.
_EVENT_TYPES: dict[ApplicationAction, str] = {
    ApplicationAction.RESEND_INVITATION: "invitation_resent",
    ApplicationAction.CONTACT: "application_contacted",
    ApplicationAction.START_TRIAL: "trial_started",
    ApplicationAction.PROMOTE: "application_approved",
    ApplicationAction.REJECT: "application_rejected",
    ApplicationAction.REOPEN: "application_reopened",
    ApplicationAction.RESTORE_VOLUNTEER: "application_volunteer_restored",
}

# ── Domain event ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DomainEventRecord:
    event_type: str
    actor_user_account_id: int | None
    subject_type: str
    subject_id: int
    payload: dict
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: int | None = None
    trace_id: str | None = None


# ── Transition result ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TransitionResult:
    new_state: ApplicationState
    event: DomainEventRecord | None = None


# ── Transition context ─────────────────────────────────────────────

# Contextual data that guards need but that isn't part of the state machine
# itself, such as whether the applicant has submitted their details.


@dataclass(frozen=True, slots=True)
class TransitionContext:
    actor_user_account_id: int | None = None
    # Promotion requires the applicant-facing profile step, not merely the
    # partial submission row created during public signup.
    has_complete_profile: bool = True


# ── Public API ─────────────────────────────────────────────────────


def application_transition(
    state: ApplicationState,
    action: ApplicationAction,
    *,
    context: TransitionContext | None = None,
) -> TransitionResult:
    """Compute the result of applying *action* to *state*.

    Raises ``IllegalTransition`` if the transition is not allowed.
    """
    ctx = context or TransitionContext()

    # Deletion is reserved for records that have not entered an operational
    # lifecycle. The service adds the profile/submission guard.
    if action in _DELETE_ACTIONS and state == ApplicationState.NEW:
        return TransitionResult(
            new_state=state,
            event=DomainEventRecord(
                event_type="application_deleted",
                actor_user_account_id=ctx.actor_user_account_id,
                subject_type="application",
                subject_id=0,  # caller fills this in
                payload={"previous_state": state.value},
            ),
        )

    # Promotion requires applicant details to create the volunteer.
    if action == ApplicationAction.PROMOTE:
        if not ctx.has_complete_profile:
            raise IllegalTransition(
                "Cannot approve an application before the full profile is complete."
            )

    # Standard transitions.
    new_state = _TRANSITIONS.get((state, action))
    if new_state is None:
        raise IllegalTransition(
            f"Cannot apply action '{action.value}' to state '{state.value}'."
        )

    if action == ApplicationAction.SUBMIT_PROFILE:
        # A promoted applicant re-submitting is completing their volunteer
        # profile, not re-applying.
        event_type = (
            "profile_completed"
            if state in (ApplicationState.TRIAL, ApplicationState.VOLUNTEER)
            else "application_submitted"
        )
    else:
        event_type = _EVENT_TYPES[action]

    return TransitionResult(
        new_state=new_state,
        event=DomainEventRecord(
            event_type=event_type,
            actor_user_account_id=ctx.actor_user_account_id,
            subject_type="application",
            subject_id=0,  # caller fills this in
            payload={"previous_state": state.value},
        ),
    )
