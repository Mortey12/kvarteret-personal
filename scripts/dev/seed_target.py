"""The safety checks every dev seed script runs before it writes.

Two independent checks, because neither is enough alone:

1. **Where.** A script may seed a database on this machine (``localhost`` / ``127.0.0.1``). A hosted
   database is seeded only on purpose: ``DEMO_SEED_CONFIRM`` must equal the whole target, written
   ``user@host:port/database`` (no password). The host name alone is not enough: Supabase's pooler
   host is shared by every project in a region, and the user name (``postgres.<project>``) is what
   tells two projects apart.
2. **What is already there.** Whatever the URL says, a seed function refuses when the database holds
   a volunteer or a user account that is not fake (an email that does not end in ``@example.dev``;
   the configured dev admin account is allowed). A tunnel on ``localhost`` that leads to a real
   database passes the first check, and stops here.

Nothing is written when a check fails. This is still a guard against mistakes, not a lock: someone
who sets the confirmation for a real database on purpose, with only fake rows in it, can seed it.
"""

from __future__ import annotations

import os

from sqlalchemy import func, or_, select
from sqlalchemy.engine import make_url

from app.db.tables import user_accounts, volunteer_records

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
CONFIRM_ENV = "DEMO_SEED_CONFIRM"
FAKE_DOMAIN = "@example.dev"
DEV_ADMIN_DEFAULT = "dev@kvarteret.dev"


class SeedRefused(RuntimeError):
    """A safety check failed; nothing was written."""


def database_host(database_url: str) -> str | None:
    return make_url(database_url).host


def target_fingerprint(database_url: str) -> str:
    """``user@host:port/database`` of a URL, without the password."""
    url = make_url(database_url)
    port = f":{url.port}" if url.port else ""
    return f"{url.username or ''}@{url.host or ''}{port}/{url.database or ''}"


def check_database_target(database_url: str, confirm: str | None) -> str:
    """The host to seed, or a refusal. A hosted database needs DEMO_SEED_CONFIRM=<whole target>."""
    host = database_host(database_url)
    if not host:
        raise SeedRefused("DATABASE_URL has no host name, so it cannot be confirmed.")
    if host in LOCAL_HOSTS:
        return host
    expected = target_fingerprint(database_url)
    if confirm != expected:
        raise SeedRefused(
            f"{host} is not local. To seed this database on purpose, set {CONFIRM_ENV}={expected}."
        )
    return host


async def ensure_only_fake_people(session) -> None:
    """Refuse when the database holds anyone who could be a real person.

    A volunteer with no email or an email outside ``@example.dev`` counts, and so does a user
    account, except the dev admin (``DEV_ADMIN_EMAIL``, ``dev@kvarteret.dev`` by default).
    """
    volunteer_email = func.lower(volunteer_records.c.email)
    volunteers = await session.scalar(
        select(func.count())
        .select_from(volunteer_records)
        .where(or_(volunteer_records.c.email.is_(None), ~volunteer_email.like(f"%{FAKE_DOMAIN}")))
    )
    if volunteers:
        raise SeedRefused(
            f"{volunteers} volunteer(s) have no email or an email that does not end in {FAKE_DOMAIN}. "
            "This script only runs on fake data."
        )
    dev_admin = os.environ.get("DEV_ADMIN_EMAIL", DEV_ADMIN_DEFAULT).lower()
    user_email = func.lower(user_accounts.c.email)
    users = await session.scalar(
        select(func.count())
        .select_from(user_accounts)
        .where(
            user_email != dev_admin,
            or_(user_accounts.c.email.is_(None), ~user_email.like(f"%{FAKE_DOMAIN}")),
        )
    )
    if users:
        raise SeedRefused(
            f"{users} user account(s) have no email or an email that does not end in {FAKE_DOMAIN} "
            "(the dev admin is allowed). This script only runs on fake data."
        )
