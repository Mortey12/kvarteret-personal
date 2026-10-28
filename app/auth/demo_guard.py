"""The database check behind demo login: every person in the database must be fake.

Kept apart from ``demo_login.py`` so domain services can use the demo helpers without
importing every table.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.auth.demo_login import DEMO_EMAIL_DOMAIN, DemoLoginRefused
from app.db.repository import SqlAlchemyRepository
from app.db.tables import user_accounts, volunteer_records
from app.observability import get_logger

logger = get_logger(__name__)


class DemoDatabaseGuard(SqlAlchemyRepository):
    """Refuses demo login unless every volunteer and user has an @example.dev email.

    Runs at startup (``app_lifespan``) and again before the first demo login in each
    process, so a runtime that skips the startup hook is still covered. A passed check
    is remembered for the life of the process.
    """

    def __init__(self) -> None:
        self._passed = False

    async def count_real_people(self) -> int:
        """Volunteers and user accounts with no email or one outside @example.dev."""
        total = 0
        for table in (volunteer_records, user_accounts):
            email = func.lower(func.coalesce(table.c.email, ""))
            total += await self.session.scalar(
                select(func.count())
                .select_from(table)
                .where(~email.like(f"%{DEMO_EMAIL_DOMAIN}"))
            ) or 0
        return total

    async def ensure_only_fake_people(self) -> None:
        if self._passed:
            return
        count = await self.count_real_people()
        if count:
            logger.error(
                "auth.demo_login.refused",
                extra={"reason": "real_people", "count": count},
            )
            raise DemoLoginRefused(
                f"Demo login is on, but {count} volunteer(s) or user account(s) have no "
                f"email or an email that does not end in {DEMO_EMAIL_DOMAIN}. Demo login "
                "only runs against a database with fake people. Turn DEMO_LOGIN_ENABLED off."
            )
        self._passed = True
