"""Exhaustive tests for the volunteer application state machine."""

import pytest

from app.domain.volunteer_applications.state_machine import (
    ApplicationAction,
    ApplicationState,
    IllegalTransition,
    TransitionContext,
    application_transition,
)


EXPECTED_APPLICATION_TRANSITIONS = {
    (ApplicationState.NEW, ApplicationAction.SUBMIT_PROFILE): ApplicationState.NEW,
    (ApplicationState.NEW, ApplicationAction.RESEND_INVITATION): ApplicationState.NEW,
    (ApplicationState.NEW, ApplicationAction.CONTACT): ApplicationState.CONTACTED,
    (ApplicationState.NEW, ApplicationAction.REJECT): ApplicationState.NOT_VOLUNTEER,
    (ApplicationState.NEW, ApplicationAction.DELETE): ApplicationState.NEW,
    (ApplicationState.CONTACTED, ApplicationAction.SUBMIT_PROFILE): ApplicationState.CONTACTED,
    (ApplicationState.CONTACTED, ApplicationAction.RESEND_INVITATION): ApplicationState.CONTACTED,
    (ApplicationState.CONTACTED, ApplicationAction.START_TRIAL): ApplicationState.TRIAL,
    (ApplicationState.CONTACTED, ApplicationAction.REJECT): ApplicationState.NOT_VOLUNTEER,
    (ApplicationState.TRIAL, ApplicationAction.SUBMIT_PROFILE): ApplicationState.TRIAL,
    (ApplicationState.TRIAL, ApplicationAction.PROMOTE): ApplicationState.VOLUNTEER,
    (ApplicationState.TRIAL, ApplicationAction.REJECT): ApplicationState.NOT_VOLUNTEER,
    (ApplicationState.VOLUNTEER, ApplicationAction.SUBMIT_PROFILE): ApplicationState.VOLUNTEER,
    (ApplicationState.VOLUNTEER, ApplicationAction.REJECT): ApplicationState.NOT_VOLUNTEER,
    (ApplicationState.NOT_VOLUNTEER, ApplicationAction.REOPEN): ApplicationState.CONTACTED,
    (ApplicationState.NOT_VOLUNTEER, ApplicationAction.RESTORE_VOLUNTEER): ApplicationState.VOLUNTEER,
}


@pytest.mark.parametrize(
    "state,action",
    [(state, action) for state in ApplicationState for action in ApplicationAction],
)
def test_application_transition_matrix(state, action):
    expected = EXPECTED_APPLICATION_TRANSITIONS.get((state, action))
    if expected is None:
        with pytest.raises(IllegalTransition):
            application_transition(state, action)
    else:
        assert application_transition(state, action).new_state == expected


def test_contact_and_trial_start_emit_audit_events():
    contacted = application_transition(ApplicationState.NEW, ApplicationAction.CONTACT)
    trial = application_transition(ApplicationState.CONTACTED, ApplicationAction.START_TRIAL)

    assert contacted.event is not None
    assert contacted.event.event_type == "application_contacted"
    assert trial.event is not None
    assert trial.event.event_type == "trial_started"


def test_trial_profile_submission_is_profile_completion():
    result = application_transition(ApplicationState.TRIAL, ApplicationAction.SUBMIT_PROFILE)

    assert result.new_state == ApplicationState.TRIAL
    assert result.event is not None
    assert result.event.event_type == "profile_completed"


def test_promote_emits_approval_event():
    result = application_transition(ApplicationState.TRIAL, ApplicationAction.PROMOTE)

    assert result.event is not None
    assert result.event.event_type == "application_approved"


def test_promote_requires_complete_profile():
    with pytest.raises(IllegalTransition, match="full profile is complete"):
        application_transition(
            ApplicationState.TRIAL,
            ApplicationAction.PROMOTE,
            context=TransitionContext(has_complete_profile=False),
        )


def test_promote_is_independent_of_friend_relationships():
    result = application_transition(
        ApplicationState.TRIAL,
        ApplicationAction.PROMOTE,
    )

    assert result.new_state == ApplicationState.VOLUNTEER


def test_terminal_decision_can_be_reconsidered():
    rejected = application_transition(ApplicationState.VOLUNTEER, ApplicationAction.REJECT)
    reopened = application_transition(rejected.new_state, ApplicationAction.REOPEN)

    assert rejected.new_state == ApplicationState.NOT_VOLUNTEER
    assert reopened.new_state == ApplicationState.CONTACTED
    assert application_transition(
        rejected.new_state, ApplicationAction.RESTORE_VOLUNTEER
    ).new_state == ApplicationState.VOLUNTEER
