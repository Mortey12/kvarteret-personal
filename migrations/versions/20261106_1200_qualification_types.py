"""Skiftlista: the qualifications a leader can train people for are rows, not code.

Adds ``qualification_types`` (key, label, style, active, optional group, order), seeded with the
three that exist (skjenker, skiftleder, kokk), and drops the database check that only allowed
those three names on ``volunteer_qualifications``: the names are checked against the new table by
the service. Kokk is limited to the group named "Kjøkken" when there is one.

Revision ID: 20261106_1200
Revises: 20261105_1200
"""

from alembic import op
import sqlalchemy as sa

revision = "20261106_1200"
down_revision = "20261105_1200"
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
        "qualification_types",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("style", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="qualification_types_key_key"),
        sa.CheckConstraint("style IN ('normal', 'skiftleder')", name="qualification_types_style_valid"),
        schema="public",
    )
    bind = op.get_bind()
    schema = "public." if bind.dialect.name == "postgresql" else ""
    for order, (key, label, style) in enumerate(
        (("skjenker", "skjenker", "normal"), ("skiftleder", "skiftleder", "skiftleder"), ("kokk", "kokk", "normal"))
    ):
        bind.execute(
            sa.text(
                f"INSERT INTO {schema}qualification_types (key, label, style, sort_order) "
                "VALUES (:key, :label, :style, :order)"
            ),
            {"key": key, "label": label, "style": style, "order": order},
        )
    # Kokk is for the kitchen only, when the kitchen is a group of its own.
    bind.execute(
        sa.text(
            f"UPDATE {schema}qualification_types SET group_id = "
            f"(SELECT min(id) FROM {schema}groups WHERE lower(name) = 'kjøkken') WHERE key = 'kokk'"
        )
    )
    _lock_down("qualification_types")
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "volunteer_qualifications_qualification_valid", "volunteer_qualifications", type_="check", schema="public"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_check_constraint(
            "volunteer_qualifications_qualification_valid",
            "volunteer_qualifications",
            "qualification IN ('skjenker', 'skiftleder', 'kokk')",
            schema="public",
        )
    op.drop_table("qualification_types", schema="public")
