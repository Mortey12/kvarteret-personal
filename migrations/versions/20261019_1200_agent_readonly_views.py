"""Read-only views and role for the admin Skonk chat.

Revision ID: 20261019_1200
Revises: 20261006_1500

The chat runs model-written SELECTs as ``agent_reader`` (NOLOGIN, SELECT on
the ``agent`` schema only). Views omit secrets such as invite and card tokens.
"""

from alembic import op

revision = "20261019_1200"
down_revision = "20261006_1500"
branch_labels = None
depends_on = None

_VIEWS = {
    "volunteers": "SELECT id, first_name, last_name, email, phone, birth_date, "
    "gender, street_address, postal_code, created_at FROM public.volunteer_records",
    "groups": "SELECT id, slug, name, description, is_active, active_through_semester, "
    "parent_group_id, discount_tier, created_at FROM public.groups",
    "assignment_roles": "SELECT id, name, group_id, penguin_points FROM public.assignment_roles",
    "role_assignments": "SELECT id, volunteer_id, group_id, role_id, semester, "
    "contract_signed, created_at FROM public.role_assignments",
    "courses": "SELECT id, name, description FROM public.courses",
    "course_completions": "SELECT id, volunteer_id, course_id, completed_semester "
    "FROM public.course_completions",
    "group_course_requirements": "SELECT course_id, group_id FROM public.group_course_requirements",
    "applications": "SELECT id, email, source, status, initial_group_id, initial_role_id, "
    "first_choice_group_id, second_choice_group_id, first_choice_label, "
    "second_choice_label, trial_shift_attended, trial_shift_marked_at, "
    "trial_started_at, trial_ends_at, full_profile_submitted_at, "
    "promoted_volunteer_id, promoted_at, trial_assignment_id, created_at "
    "FROM public.volunteer_application_invites",
    "application_submissions": "SELECT id, invite_id AS application_id, first_name, "
    "last_name, email, gender, birth_date, street_address, postal_code, phone, "
    "studiested, bakgrunn, created_at FROM public.volunteer_application_submissions",
    "volunteer_counts": "SELECT metric, semester, scope_key, group_id, "
    "CASE WHEN is_suppressed THEN NULL ELSE volunteer_count END AS volunteer_count, "
    "is_suppressed FROM public.warehouse_volunteer_counts",
}


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_reader') "
        "THEN CREATE ROLE agent_reader NOLOGIN NOINHERIT; END IF; END $$"
    )
    # The connecting role must be able to SET ROLE agent_reader.
    op.execute("GRANT agent_reader TO CURRENT_USER")
    op.execute("CREATE SCHEMA IF NOT EXISTS agent")
    for name, select in _VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW agent.{name} AS {select}")
    op.execute("REVOKE ALL ON SCHEMA agent FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA agent TO agent_reader")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA agent TO agent_reader")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS agent CASCADE")
    op.execute("DROP ROLE IF EXISTS agent_reader")
