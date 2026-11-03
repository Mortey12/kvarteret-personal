"""CSRF tokens (signed double submit). Lives in the shared layer so both the web pages and the JSON
API (``app.api.v1.leader_write_guard``) can use it; ``app.web.csrf`` re-exports it."""

from __future__ import annotations

from secrets import compare_digest, token_urlsafe

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import Settings

CSRF_COOKIE_NAME = "kvarteret_csrf"
CSRF_FIELD_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


class CsrfTokenService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._serializer = URLSafeSerializer(
            settings.app_secret_key, salt="kvarteret-csrf"
        )

    def issue_token(self) -> str:
        return self._serializer.dumps({"nonce": token_urlsafe(32)})

    def is_valid_token(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            payload = self._serializer.loads(token)
        except BadSignature:
            return False
        return (
            isinstance(payload, dict)
            and isinstance(payload.get("nonce"), str)
            and bool(payload["nonce"])
        )

    def tokens_match(
        self, cookie_token: str | None, submitted_token: str | None
    ) -> bool:
        if not self.is_valid_token(cookie_token) or not self.is_valid_token(
            submitted_token
        ):
            return False
        assert cookie_token is not None
        assert submitted_token is not None
        return compare_digest(cookie_token, submitted_token)

    def set_cookie(self, response: Response, request: Request, token: str) -> None:
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=request.url.scheme == "https"
            or self.settings.app_env == "production",
            samesite="lax",
        )
