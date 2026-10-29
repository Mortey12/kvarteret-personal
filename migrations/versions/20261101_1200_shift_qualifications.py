"""Skiftlista 5d, migration A: qualifications and training, schema only.

Creates ``volunteer_qualifications`` and the columns the new code needs. It
copies no data: verv is copied into qualifications by the separate script
``scripts/shifts/copy_verv_to_qualifications.py``, run on its own after the
bars and the kitchen have templates or settings rows. Nothing is removed or
moved here: the ``opplaering`` slots and ``training_replaces_slot`` stay until
the later revisions that go with step 3 of 5d.

Revision ID: 20261101_1200
Revises: 20261031_1200
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261101_1200"
down_revision = "20261031_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "volunteer_qualifications",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "volunteer_id",
            sa.BigInteger(),
            sa.ForeignKey("public.volunteer_records.id"),
            nullable=False,
        ),
        sa.Column("qualification", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), nullable=False
        ),
        sa.Column("training_target", sa.Integer(), nullable=True),
        sa.Column("takes_trainees", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "volunteer_id",
            "qualification",
            "group_id",
            name="volunteer_qualifications_volunteer_qualification_group_key",
        ),
        sa.CheckConstraint(
            "qualification IN ('skjenker', 'skiftleder', 'kokk')",
            name="volunteer_qualifications_qualification_valid",
        ),
        sa.CheckConstraint(
            "status IN ('training', 'approved')",
            name="volunteer_qualifications_status_valid",
        ),
        schema="public",
    )
    op.create_index(
        "volunteer_qualifications_group_idx",
        "volunteer_qualifications",
        ["group_id"],
        schema="public",
    )
    op.execute("ALTER TABLE public.volunteer_qualifications ENABLE ROW LEVEL SECURITY")
    op.execute(
        "REVOKE ALL ON public.volunteer_qualifications FROM PUBLIC, anon, authenticated"
    )

    op.add_column(
        "shift_assignments", sa.Column("training_for", sa.Text(), nullable=True), schema="public"
    )
    op.add_column(
        "shift_group_settings",
        sa.Column(
            "training_shifts_needed", sa.Integer(), nullable=False, server_default="3"
        ),
        schema="public",
    )
    op.add_column(
        "shift_group_settings",
        sa.Column(
            "sibling_skiftleder_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="public",
    )
    # Already true from the first shift migration; stated again so the new code
    # may insert without the column before migration B drops it.
    for table_name in ("shift_templates", "shifts"):
        op.alter_column(
            table_name,
            "training_replaces_slot",
            server_default=sa.true(),
            schema="public",
        )


def downgrade() -> None:
    op.drop_column("shift_group_settings", "sibling_skiftleder_allowed", schema="public")
    op.drop_column("shift_group_settings", "training_shifts_needed", schema="public")
    op.drop_column("shift_assignments", "training_for", schema="public")
    op.drop_table("volunteer_qualifications", schema="public")
