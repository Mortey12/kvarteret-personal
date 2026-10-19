"""Add mobile-card refresh tokens so the app can renew sessions without a new code.

Revision ID: 20261020_1200
Revises: 20261019_1200
"""

from alembic import op
import sqlalchemy as sa

revision = "20261020_1200"
down_revision = "20261019_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mobile_card_refresh_tokens",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column(
            "volunteer_id",
            sa.BigInteger(),
            sa.ForeignKey("public.volunteer_records.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "application_id",
            sa.BigInteger(),
            sa.ForeignKey("public.volunteer_application_invites.id", ondelete="CASCADE"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(volunteer_id IS NULL) <> (application_id IS NULL)",
            name="mobile_card_refresh_tokens_one_subject",
        ),
        schema="public",
    )
    op.create_index(
        "mobile_card_refresh_tokens_expiry_idx",
        "mobile_card_refresh_tokens",
        ["expires_at"],
        schema="public",
    )
    op.execute("ALTER TABLE public.mobile_card_refresh_tokens ENABLE ROW LEVEL SECURITY")
    op.execute(
        "REVOKE ALL ON public.mobile_card_refresh_tokens FROM PUBLIC, anon, authenticated"
    )


def downgrade() -> None:
    op.drop_table("mobile_card_refresh_tokens", schema="public")
