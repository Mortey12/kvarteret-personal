"""The one safety check every dev seed script runs before it connects.

A script may seed a database on this machine (``localhost`` / ``127.0.0.1``). A hosted database
is seeded only on purpose: ``DEMO_SEED_CONFIRM`` must be set to exactly that host name. Nothing
is written when the check fails.

This cannot know which hosted database is production, so it does not make a production database
safe by itself. It makes sure no seed script reaches any hosted database by accident, for example
through a ``DATABASE_URL`` left over in the shell.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
CONFIRM_ENV = "DEMO_SEED_CONFIRM"


class SeedRefused(RuntimeError):
    """A safety check failed; nothing was written."""


def database_host(database_url: str) -> str | None:
    return make_url(database_url).host


def check_database_target(database_url: str, confirm: str | None) -> str:
    """The host to seed, or a refusal. A hosted database needs DEMO_SEED_CONFIRM=<host>."""
    host = database_host(database_url)
    if not host:
        raise SeedRefused("DATABASE_URL has no host name, so it cannot be confirmed.")
    if host in LOCAL_HOSTS:
        return host
    if confirm != host:
        raise SeedRefused(f"{host} is not local. To seed this database on purpose, set {CONFIRM_ENV}={host}.")
    return host
