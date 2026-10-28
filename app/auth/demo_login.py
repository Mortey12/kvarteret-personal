"""Demo login for a hosted demo that holds fake data only.

Off unless ``DEMO_LOGIN_ENABLED=true``, ``DEMO_ACCESS_CODE`` (at least 12
characters) and ``DEMO_APP_CODE`` (exactly 6 digits) are set;
``validate_production_secrets`` refuses a missing or malformed code. When on, an
email ending in ``@example.dev`` logs in to the app with ``DEMO_APP_CODE`` (the
app's code box only takes 6 digits) instead of an emailed code, and the demo leader
(``demo.leder@example.dev``) logs in to the leader pages as Admin at
``/login/demo``.

The app refuses to start, and every demo login is refused, while any volunteer or
user account has an email outside ``@example.dev`` (``demo_guard.py``). Never
enable this on Kvarteret's real deployment.
"""

from __future__ import annotations

import hmac

from app.config import Settings
from app.observability import get_logger

DEMO_EMAIL_DOMAIN = "@example.dev"
DEMO_LEADER_EMAIL = f"demo.leder{DEMO_EMAIL_DOMAIN}"

logger = get_logger(__name__)


class DemoLoginRefused(RuntimeError):
    """The database may hold real people, so demo login must not run."""


def demo_login_enabled(settings: Settings) -> bool:
    return settings.demo_login_enabled and bool(settings.demo_access_code)


def is_demo_email(email: str) -> bool:
    normalized = email.strip().lower()
    return normalized.endswith(DEMO_EMAIL_DOMAIN) and len(normalized) > len(DEMO_EMAIL_DOMAIN)


def demo_code_matches(settings: Settings, access_code: str) -> bool:
    """Constant-time check of a submitted code against DEMO_ACCESS_CODE (leader login)."""
    if not demo_login_enabled(settings):
        return False
    assert settings.demo_access_code is not None
    return hmac.compare_digest(
        access_code.strip().encode("utf-8"), settings.demo_access_code.encode("utf-8")
    )


def demo_app_code_matches(settings: Settings, access_code: str) -> bool:
    """Constant-time check of a submitted code against DEMO_APP_CODE (app login)."""
    if not demo_login_enabled(settings) or not settings.demo_app_code:
        return False
    return hmac.compare_digest(
        access_code.strip().encode("utf-8"), settings.demo_app_code.encode("utf-8")
    )


def demo_account_name(email: str) -> str:
    """The part before ``@example.dev``: what the logs record (they redact whole emails)."""
    return email.strip().lower().removesuffix(DEMO_EMAIL_DOMAIN)


def log_demo_login(email: str, *, kind: str) -> None:
    """One line per demo login; never the code."""
    logger.info(
        "auth.demo_login.succeeded",
        extra={"demo_account": demo_account_name(email), "login_kind": kind},
    )
