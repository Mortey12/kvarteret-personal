# ADR-003: Domain event audit log

**Status**: Accepted
**Date**: 2026-06-10
**Author**: Pi

## Context

The volunteer application lifecycle is the most important business process in
`kvarteret-personal`.  Every state change — invitation, submission, approval,
rejection, trial shift, and deletion — must be auditable
so that administrators can reconstruct who did what and when.

The domain already follows a "history as truth" pattern: `role_assignments`
(formerly `historie`) is an append-only table from which "currently active
volunteer" is derived.  The registration tables (`volunteer_application_invites`,
`volunteer_application_submissions`, and friend-invitation relationship records
carry timestamps (`created_at`, `promoted_at`, `trial_shift_marked_at`)
but these record *when* without recording *who* acted or *what* the previous
state was.

## Decision

We add an append-only `domain_events` table that records every business-meaningful
state transition as a row.  The row is inserted in the same database transaction
as the state change itself, so the event and the state are atomically consistent.

**Table schema** (`domain_events`, owned by `volunteer_applications`):

- `id` (bigint, PK)
- `event_type` (text) — e.g. `application_submitted`, `application_approved`,
  `application_rejected`, `application_deleted`, `trial_shift_marked`,
- `actor_user_account_id` (bigint, nullable) — the admin user who performed the
  action, or `NULL` for automated/public actions
- `subject_type` (text) — e.g. `application`
- `subject_id` (bigint) — the primary key of the subject row
- `payload` (jsonb) — free-form metadata, minimally `{"previous_state": "..."}`
- `occurred_at` (timestamptz) — when the event was recorded

**State remains the source of truth.**  We do not adopt event sourcing: there
is no projection or replay machinery.  The `status` column on
`volunteer_application_invites` is the authoritative state; `domain_events` is
an audit trail, not a state store.

**Events are emitted by the state machine.**  `app/domain/volunteer_applications/state_machine.py`
returns a `DomainEventRecord` as part of every `TransitionResult`.  The workflow
coordinator inserts the event row in the same transaction as the state change.

**Side effects are chosen by the workflow, not the state machine.**  The
original design had `TransitionResult.effects` return named `Effect` records,
but the workflow never executed them; emails are enqueued by the workflow in
the same transaction (`email_delivery` outbox, keyed by the domain event id)
and dispatched after commit.  The unused `Effect` records were removed
(2026-10) so the state machine only decides the new state and the audit event.

**Transitions are compare-and-set.**  The workflow validates a transition
against the status it read, then the write re-checks that status after the
per-email advisory lock and in the `UPDATE ... WHERE status = :expected`
clause.  A concurrent transition fails with a conflict instead of producing
two contradictory events.

## Alternatives considered

**Full event sourcing (rejected).**  Projecting state from an event stream
would make the most important business flow harder to read and would impose a
decade-long event-schema-evolution tax that a rotating volunteer team cannot
pay.  The useful halves — auditable transitions and named side effects — are
adopted without the replay machinery.

**No audit table (rejected).**  The status column alone cannot record *who*
made a change or what the previous state was.  The timestamps on the
registration tables are spread across multiple columns and tables, making a
coherent timeline difficult to reconstruct.

## Consequences

- Every state transition produces exactly one `domain_events` row.
- The state machine (`state_machine.py`) is the single place that decides what
  event type and payload to emit (`_EVENT_TYPES`).
- Adding a new transition type requires adding it to the state machine and
  the transition table — both in one file.
- The `payload` jsonb column allows forward-compatible metadata without schema
  changes.
- When other modules later emit events (e.g. volunteer record changes), the
  `domain_events` table ownership moves to a small shared `audit` module.
