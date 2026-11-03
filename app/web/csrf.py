"""CSRF tokens for the server-rendered pages. The implementation is in ``app.shared.csrf`` (shared
with the JSON API); the names are re-exported here so existing imports keep working."""

from app.shared.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_FIELD_NAME,
    CSRF_HEADER_NAME,
    CsrfTokenService,
)

__all__ = ["CSRF_COOKIE_NAME", "CSRF_FIELD_NAME", "CSRF_HEADER_NAME", "CsrfTokenService"]
