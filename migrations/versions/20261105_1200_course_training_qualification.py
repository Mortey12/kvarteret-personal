"""Courses: which course is registered by approving a finished training.

Adds ``courses.training_qualification`` (skjenker, kokk or skiftleder; null for every other
course) with at most one course per qualification, and marks the three existing training courses
by name so nothing changes for them. After this the link lives in the data, not in the code.

Revision ID: 20261105_1200
Revises: 20261104_1200
"""

from alembic import op
import sqlalchemy as sa

revision = "20261105_1200"
down_revision = "20261104_1200"
branch_labels = None
depends_on = None

# (course name in lower case, qualification) for the courses that exist today.
EXISTING = (("skjenkekurs", "skjenker"), ("kokkekurs", "kokk"), ("skiftlederkurs", "skiftleder"))


def upgrade() -> None:
    op.add_column("courses", sa.Column("training_qualification", sa.Text(), nullable=True), schema="public")
    op.create_index(
        "courses_training_qualification_key",
        "courses",
        ["training_qualification"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("training_qualification IS NOT NULL"),
        sqlite_where=sa.text("training_qualification IS NOT NULL"),
    )
    bind = op.get_bind()
    for name, qualification in EXISTING:
        # The oldest course with that name; skipped if the name is not found or is not unique.
        bind.execute(
            sa.text(
                "UPDATE public.courses SET training_qualification = :q "
                "WHERE id = (SELECT min(id) FROM public.courses WHERE lower(name) = :name) "
                "AND training_qualification IS NULL"
            ).execution_options(schema_translate_map=None)
            if bind.dialect.name == "postgresql"
            else sa.text(
                "UPDATE courses SET training_qualification = :q "
                "WHERE id = (SELECT min(id) FROM courses WHERE lower(name) = :name) "
                "AND training_qualification IS NULL"
            ),
            {"q": qualification, "name": name},
        )


def downgrade() -> None:
    op.drop_index("courses_training_qualification_key", table_name="courses", schema="public")
    op.drop_column("courses", "training_qualification", schema="public")
