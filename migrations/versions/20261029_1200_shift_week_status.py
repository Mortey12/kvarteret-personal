"""Skiftlista: allocation status per week instead of per month.

Revision ID: 20261029_1200
Revises: 20261028_1200
"""

from datetime import date, timedelta

from alembic import op
import sqlalchemy as sa

revision = "20261029_1200"
down_revision = "20261028_1200"
branch_labels = None
depends_on = None

# Least published first: a month made of mixed weeks goes back to the
# status that hides the most.
_STRICTNESS = ("open", "allocating", "published")


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _as_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _mondays_in_month(month: date) -> list[date]:
    day = month + timedelta(days=(7 - month.weekday()) % 7)
    mondays = []
    while day.month == month.month:
        mondays.append(day)
        day += timedelta(days=7)
    return mondays


def upgrade() -> None:
    bind = op.get_bind()
    constraints: list[sa.CheckConstraint] = [
        sa.CheckConstraint(
            "status IN ('open', 'allocating', 'published')",
            name="shift_week_status_status_values",
        )
    ]
    if bind.dialect.name == "postgresql":
        constraints.append(
            sa.CheckConstraint(
                "EXTRACT(ISODOW FROM week_start) = 1", name="shift_week_status_is_monday"
            )
        )
    op.create_table(
        "shift_week_status",
        sa.Column(
            "group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), primary_key=True
        ),
        sa.Column("week_start", sa.Date(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        _updated_at(),
        *constraints,
        schema="public",
    )

    months = bind.execute(
        sa.text("SELECT group_id, month, status FROM public.shift_month_status")
    ).all()
    rows = [
        {"group_id": group_id, "week_start": monday, "status": status}
        for group_id, month, status in months
        for monday in _mondays_in_month(_as_date(month))
    ]
    if rows:
        bind.execute(
            sa.text(
                "INSERT INTO public.shift_week_status (group_id, week_start, status) "
                "VALUES (:group_id, :week_start, :status)"
            ),
            rows,
        )
    op.drop_table("shift_month_status", schema="public")


def downgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "shift_month_status",
        sa.Column(
            "group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), primary_key=True
        ),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('open', 'allocating', 'published')",
            name="shift_month_status_status_values",
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM month) = 1" if bind.dialect.name == "postgresql" else "1 = 1",
            name="shift_month_status_first_of_month",
        ),
        schema="public",
    )
    weeks = bind.execute(
        sa.text("SELECT group_id, week_start, status FROM public.shift_week_status")
    ).all()
    months: dict[tuple[int, date], str] = {}
    for group_id, week_start, status in weeks:
        key = (group_id, _as_date(week_start).replace(day=1))
        current = months.get(key)
        if current is None or _STRICTNESS.index(status) < _STRICTNESS.index(current):
            months[key] = status
    if months:
        bind.execute(
            sa.text(
                "INSERT INTO public.shift_month_status (group_id, month, status) "
                "VALUES (:group_id, :month, :status)"
            ),
            [{"group_id": g, "month": m, "status": st} for (g, m), st in months.items()],
        )
    op.drop_table("shift_week_status", schema="public")
