"""Skiftlista: pending suggestions for places, apart from the real assignments.

Revision ID: 20261104_1200
Revises: 20261103_1200
"""

from alembic import op
import sqlalchemy as sa

revision = "20261104_1200"
down_revision = "20261103_1200"
branch_labels = None
depends_on = None


def _lock_down(table_name: str) -> None:
    """Row-level security on and no access for the public Supabase roles, like every other table
    here. Postgres only: the roles exist in Supabase, and the unit tests run on SQLite."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON public.{table_name} FROM PUBLIC, anon, authenticated")


def upgrade() -> None:
    op.create_table(
        "shift_suggestions",
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
            sa.ForeignKey("public.volunteer_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("slot_id", "volunteer_id", name="shift_suggestions_slot_volunteer_key"),
        schema="public",
    )
    op.create_index("ix_shift_suggestions_slot_id", "shift_suggestions", ["slot_id"], schema="public")
    _lock_down("shift_suggestions")


def downgrade() -> None:
    op.drop_index("ix_shift_suggestions_slot_id", table_name="shift_suggestions", schema="public")
    op.drop_table("shift_suggestions", schema="public")
