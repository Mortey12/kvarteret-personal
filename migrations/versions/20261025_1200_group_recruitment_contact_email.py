"""Let each group set a recruitment contact (email and/or person) for the daily digest."""

from alembic import op
import sqlalchemy as sa

revision = "20261025_1200"
down_revision = "20261020_1200"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "groups",
        sa.Column("recruitment_contact_email", sa.Text(), nullable=True),
        schema="public",
    )
    op.add_column(
        "groups",
        sa.Column(
            "recruitment_contact_volunteer_id",
            sa.BigInteger(),
            sa.ForeignKey("public.volunteer_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema="public",
    )


def downgrade():
    op.drop_column("groups", "recruitment_contact_volunteer_id", schema="public")
    op.drop_column("groups", "recruitment_contact_email", schema="public")
