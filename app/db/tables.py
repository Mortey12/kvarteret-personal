"""Single metadata aggregator for migrations, scripts, and the auth module.

Domain modules **own** their tables and define them in
``app/domain/{module}/tables.py``; production code imports tables
directly from the owning module, never from here. This module exists
only to pull every ``tables.py`` into one import so the tables register
on the shared ``MetaData`` — which Alembic autogeneration and the
schema-drift check need — plus the ``auth``/``storage`` schema
reflections and the ``rate_limits`` table.

This is the one db-layer module allowed to import ``app.domain.*.tables``
(see the ``.importlinter`` layers ignore). It is deliberately *not* part
of the domain-independence contract, and domain modules must not import
it — doing so would reintroduce the transitive domain→domain chains that
the per-owner imports were created to break.
"""

from app.db.metadata import public_metadata
from app.db.rate_limit import rate_limits
from app.db.table_defs.auth import (
    auth_identities,
    auth_metadata,
    auth_refresh_tokens,
    auth_sessions,
    auth_users,
)
from app.db.table_defs.storage import storage_metadata, storage_objects
from app.db.table_defs.email_delivery import email_deliveries, email_delivery_attempts
from app.db.table_defs.warehouse import warehouse_volunteer_counts
from app.domain.admin_accounts.tables import (
    group_admin_memberships,
    user_accounts,
    web_sessions,
)
from app.domain.courses.tables import (
    course_completions,
    courses,
    group_course_requirements,
)
from app.domain.event_interest.tables import event_interest
from app.domain.booking_requests.tables import booking_requests
from app.domain.groups.tables import groups
from app.domain.mobile_card.tables import (
    mobile_card_access_codes,
    mobile_card_april_state,
    mobile_card_refresh_tokens,
    mobile_card_trial_access_codes,
)
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.tables import (
    shift_assignments,
    shift_slots,
    shift_template_slots,
    shift_templates,
    shifts,
)
from app.domain.spotify.tables import integration_tokens
from app.domain.volunteer_applications.tables import (
    domain_events,
    volunteer_application_friend_invitations,
    volunteer_application_invites,
    volunteer_application_submissions,
    volunteer_prospect_idempotency_keys,
    volunteer_prospect_submissions,
)
from app.domain.volunteers.tables import (
    volunteer_cards,
    volunteer_next_of_kin,
    volunteer_photos,
    volunteer_records,
)

# Back-compat alias retained for the auth module's repository.
admin_account_group_memberships = group_admin_memberships

__all__ = [
    "admin_account_group_memberships",
    "assignment_roles",
    "auth_identities",
    "auth_metadata",
    "auth_refresh_tokens",
    "auth_sessions",
    "auth_users",
    "course_completions",
    "courses",
    "domain_events",
    "event_interest",
    "email_deliveries",
    "email_delivery_attempts",
    "group_admin_memberships",
    "group_course_requirements",
    "booking_requests",
    "groups",
    "integration_tokens",
    "mobile_card_access_codes",
    "mobile_card_april_state",
    "mobile_card_refresh_tokens",
    "mobile_card_trial_access_codes",
    "public_metadata",
    "rate_limits",
    "role_assignments",
    "shift_assignments",
    "shift_slots",
    "shift_template_slots",
    "shift_templates",
    "shifts",
    "storage_metadata",
    "storage_objects",
    "user_accounts",
    "volunteer_application_friend_invitations",
    "volunteer_application_invites",
    "volunteer_application_submissions",
    "volunteer_prospect_idempotency_keys",
    "volunteer_prospect_submissions",
    "volunteer_cards",
    "volunteer_next_of_kin",
    "volunteer_photos",
    "volunteer_records",
    "web_sessions",
    "warehouse_volunteer_counts",
]
