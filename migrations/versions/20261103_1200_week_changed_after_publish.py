"""Skiftlista: remember when and by whom a published week was changed.

Two nullable columns on shift_week_status. No default and no backfill: every existing row
stays exactly as it is ("not changed after publishing"). The status column is not touched, so
volunteers and the wish rules see no difference.

Revision ID: 20261103_1200
Revises: 20261101_1200
"""

from alembic import op
import sqlalchemy as sa

revision = "20261103_1200"
down_revision = "20261101_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shift_week_status",
        sa.Column("changed_after_publish_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "shift_week_status",
        sa.Column("changed_after_publish_by", sa.Text(), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("shift_week_status", "changed_after_publish_by", schema="public")
    op.drop_column("shift_week_status", "changed_after_publish_at", schema="public")
