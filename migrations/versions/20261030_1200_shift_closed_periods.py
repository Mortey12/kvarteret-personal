"""Skiftlista: closed periods (holidays) per group for the semester generator.

Revision ID: 20261030_1200
Revises: 20261029_1200
"""

from alembic import op
import sqlalchemy as sa

revision = "20261030_1200"
down_revision = "20261029_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shift_closed_periods",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("ends_on >= starts_on", name="shift_closed_periods_dates_ordered"),
        schema="public",
    )
    op.create_index(
        "ix_shift_closed_periods_group_id",
        "shift_closed_periods",
        ["group_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index("ix_shift_closed_periods_group_id", table_name="shift_closed_periods", schema="public")
    op.drop_table("shift_closed_periods", schema="public")
