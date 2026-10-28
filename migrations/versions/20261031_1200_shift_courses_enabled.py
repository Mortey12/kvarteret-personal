"""Skiftlista: per-group switch for the app's course checks.

Every existing row starts with the switch off. Leaders turn it on in
/shifts/settings once real course completions are registered.

Revision ID: 20261031_1200
Revises: 20261030_1200
"""

from alembic import op
import sqlalchemy as sa

revision = "20261031_1200"
down_revision = "20261030_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shift_group_settings",
        sa.Column("courses_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("shift_group_settings", "courses_enabled", schema="public")
