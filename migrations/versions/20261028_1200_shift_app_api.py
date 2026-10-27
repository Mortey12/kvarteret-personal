"""Skiftlista in the app: per-group switch, monthly status, wishes, course sessions, "kan ikke".

Revision ID: 20261028_1200
Revises: 20261027_1200
"""

from alembic import op
import sqlalchemy as sa

revision = "20261028_1200"
down_revision = "20261027_1200"
branch_labels = None
depends_on = None

_TABLES = (
    "shift_group_settings",
    "shift_month_status",
    "shift_availability_rules",
    "shift_wishes",
    "course_sessions",
    "course_session_signups",
)


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    op.add_column(
        "shift_assignments",
        sa.Column("cannot_attend", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="public",
    )
    op.add_column(
        "shift_assignments",
        sa.Column("status_message", sa.Text(), nullable=True),
        schema="public",
    )
    op.add_column(
        "shift_assignments",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )

    op.create_table(
        "shift_group_settings",
        sa.Column(
            "group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), primary_key=True
        ),
        sa.Column("app_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("samf_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("meeting_point", sa.Text(), nullable=True),
        _updated_at(),
        schema="public",
    )
    op.create_table(
        "shift_month_status",
        sa.Column(
            "group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), primary_key=True
        ),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('open', 'allocating', 'published')",
            name="shift_month_status_status_values",
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM month) = 1", name="shift_month_status_first_of_month"
        ),
        schema="public",
    )
    op.create_table(
        "shift_availability_rules",
        sa.Column(
            "volunteer_id",
            sa.BigInteger(),
            sa.ForeignKey("public.volunteer_records.id"),
            primary_key=True,
        ),
        sa.Column("semester", sa.Integer(), primary_key=True),
        sa.Column("weekday", sa.SmallInteger(), primary_key=True),
        sa.Column("answer", sa.Text(), nullable=False),
        _updated_at(),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6", name="shift_availability_rules_weekday_range"
        ),
        sa.CheckConstraint(
            "answer IN ('vil', 'kan', 'nei')", name="shift_availability_rules_answer_values"
        ),
        schema="public",
    )
    op.create_table(
        "shift_wishes",
        sa.Column(
            "shift_id",
            sa.BigInteger(),
            sa.ForeignKey("public.shifts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "volunteer_id",
            sa.BigInteger(),
            sa.ForeignKey("public.volunteer_records.id"),
            primary_key=True,
        ),
        sa.Column("answer", sa.Text(), nullable=False),
        _updated_at(),
        sa.CheckConstraint(
            "answer IN ('vil', 'kan', 'nei')", name="shift_wishes_answer_values"
        ),
        schema="public",
    )
    op.create_index(
        "shift_wishes_volunteer_idx", "shift_wishes", ["volunteer_id"], schema="public"
    )
    op.create_table(
        "course_sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "course_id", sa.BigInteger(), sa.ForeignKey("public.courses.id"), nullable=False
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("is_cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("capacity >= 0", name="course_sessions_capacity_nonnegative"),
        schema="public",
    )
    op.create_index(
        "course_sessions_course_starts_idx",
        "course_sessions",
        ["course_id", "starts_at"],
        schema="public",
    )
    op.create_table(
        "course_session_signups",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "session_id",
            sa.BigInteger(),
            sa.ForeignKey("public.course_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "volunteer_id",
            sa.BigInteger(),
            sa.ForeignKey("public.volunteer_records.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "session_id",
            "volunteer_id",
            name="course_session_signups_session_volunteer_key",
        ),
        schema="public",
    )
    op.create_index(
        "course_session_signups_volunteer_idx",
        "course_session_signups",
        ["volunteer_id"],
        schema="public",
    )
    for table_name in _TABLES:
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table_name} FROM PUBLIC, anon, authenticated")


def downgrade() -> None:
    for table_name in reversed(_TABLES):
        op.drop_table(table_name, schema="public")
    op.drop_column("shift_assignments", "status_changed_at", schema="public")
    op.drop_column("shift_assignments", "status_message", schema="public")
    op.drop_column("shift_assignments", "cannot_attend", schema="public")
