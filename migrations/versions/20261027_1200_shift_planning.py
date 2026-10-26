"""Add shift templates, shifts, places and assignments for Skiftlista.

Revision ID: 20261027_1200
Revises: 20261025_1200
"""

from alembic import op
import sqlalchemy as sa

revision = "20261027_1200"
down_revision = "20261025_1200"
branch_labels = None
depends_on = None

_TABLES = (
    "shift_templates",
    "shift_template_slots",
    "shifts",
    "shift_slots",
    "shift_assignments",
)


def upgrade() -> None:
    op.create_table(
        "shift_templates",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), nullable=False
        ),
        sa.Column("venue", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column(
            "training_replaces_slot",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="shift_templates_weekday_range"),
        schema="public",
    )
    op.create_table(
        "shift_template_slots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "template_id",
            sa.BigInteger(),
            sa.ForeignKey("public.shift_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("open_for_samf", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("count >= 0", name="shift_template_slots_count_nonnegative"),
        schema="public",
    )
    op.create_table(
        "shifts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), nullable=False
        ),
        sa.Column(
            "template_id",
            sa.BigInteger(),
            sa.ForeignKey("public.shift_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("venue", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "training_replaces_slot",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("ends_at > starts_at", name="shifts_ends_after_start"),
        schema="public",
    )
    op.create_index("shifts_starts_at_idx", "shifts", ["starts_at"], schema="public")
    op.create_index(
        "shifts_group_starts_at_idx", "shifts", ["group_id", "starts_at"], schema="public"
    )
    op.create_table(
        "shift_slots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "shift_id",
            sa.BigInteger(),
            sa.ForeignKey("public.shifts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("open_for_samf", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("count >= 0", name="shift_slots_count_nonnegative"),
        schema="public",
    )
    op.create_index("shift_slots_shift_idx", "shift_slots", ["shift_id"], schema="public")
    op.create_table(
        "shift_assignments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "slot_id",
            sa.BigInteger(),
            sa.ForeignKey("public.shift_slots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "volunteer_id",
            sa.BigInteger(),
            sa.ForeignKey("public.volunteer_records.id"),
            nullable=False,
        ),
        sa.Column("swap_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "slot_id", "volunteer_id", name="shift_assignments_slot_volunteer_key"
        ),
        schema="public",
    )
    op.create_index(
        "shift_assignments_volunteer_idx",
        "shift_assignments",
        ["volunteer_id"],
        schema="public",
    )
    for table_name in _TABLES:
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{table_name} FROM PUBLIC, anon, authenticated")


def downgrade() -> None:
    for table_name in reversed(_TABLES):
        op.drop_table(table_name, schema="public")
