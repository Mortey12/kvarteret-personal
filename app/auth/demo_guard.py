"""The database check behind demo login: every person in the database must be fake.

Kept apart from ``demo_login.py`` so domain services can use the demo helpers without
importing every table.
"""

from __future__ import annotations

import time

from sqlalchemy import func, select

from app.auth.demo_login import DEMO_EMAIL_DOMAIN, DemoLoginRefused
from app.db.repository import SqlAlchemyRepository
from app.db.tables import (
    email_deliveries,
    user_accounts,
    volunteer_application_friend_invitations,
    volunteer_application_invites,
    volunteer_application_submissions,
    volunteer_records,
)
from app.observability import get_logger

logger = get_logger(__name__)

# Every column that holds a person's email address. A fake database has only @example.dev here;
# an empty value counts as real, because it could be anyone. Add a column here when a new table
# starts to hold a person's email.
CHECKED_EMAIL_COLUMNS = (
    volunteer_records.c.email,
    user_accounts.c.email,
    volunteer_application_submissions.c.email,
    volunteer_application_invites.c.email,
    volunteer_application_friend_invitations.c.inviter_email_snapshot,
    volunteer_application_friend_invitations.c.invitee_email_snapshot,
    email_deliveries.c.recipient_email,
)

# A pass is trusted for this long. After it the next demo login looks again, so a real person who
# arrives later (a public form is still open on the demo host) is noticed within a minute.
RECHECK_AFTER_SECONDS = 60.0


class DemoDatabaseGuard(SqlAlchemyRepository):
    """Refuses demo login unless every stored email address is an @example.dev one.

    Runs at startup (``app_lifespan``) and again before each demo login that comes more than
    ``RECHECK_AFTER_SECONDS`` after the last passed check, so a runtime that skips the startup
    hook is still covered and a real person added later is noticed.
    """

    def __init__(self) -> None:
        self._passed_at: float | None = None

    @property
    def _passed(self) -> bool:
        """Whether a check passed recently enough to be trusted."""
        return (
            self._passed_at is not None
            and time.monotonic() - self._passed_at < RECHECK_AFTER_SECONDS
        )

    async def count_real_people(self) -> int:
        """Stored email addresses that are empty or outside @example.dev, in every checked column."""
        total = 0
        for column in CHECKED_EMAIL_COLUMNS:
            email = func.lower(func.coalesce(column, ""))
            total += await self.session.scalar(
                select(func.count())
                .select_from(column.table)
                .where(~email.like(f"%{DEMO_EMAIL_DOMAIN}"))
            ) or 0
        return total

    async def ensure_only_fake_people(self) -> None:
        if self._passed:
            return
        self._passed_at = None
        count = await self.count_real_people()
        if count:
            logger.error(
                "auth.demo_login.refused",
                extra={"reason": "real_people", "count": count},
            )
            raise DemoLoginRefused(
                f"Demo login is on, but {count} stored email address(es) are empty or do not "
                f"end in {DEMO_EMAIL_DOMAIN}. Demo login only runs against a database with fake "
                "people. Turn DEMO_LOGIN_ENABLED off."
            )
        self._passed_at = time.monotonic()
