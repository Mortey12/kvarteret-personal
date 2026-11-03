"""The safety checks for cookie-authenticated writes from the leader app.

Personal skips its CSRF middleware for every path under ``/api/`` (those routes were written for
bearer tokens), so a route that is authenticated by the session cookie must do its own check or a
page on another site could make a leader's browser send a write. Two checks, both required:

1. ``Origin`` must be one of ``LEADER_APP_ORIGINS``. Browsers always send it on a ``fetch`` POST,
   PUT or DELETE, and a page on another site cannot forge it.
2. ``X-CSRF-Token`` must match the ``kvarteret_csrf`` cookie (double submit). ``GET /me`` hands the
   token out; the cookie is HttpOnly, so only this app's own code can read the token from the answer.

The week's ``If-Match`` version is checked in the route, which has the week at hand.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.config import parse_origins
from app.dependencies import get_settings
from app.shared.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, CsrfTokenService


async def require_leader_write(request: Request, settings=Depends(get_settings)) -> None:
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin or origin not in parse_origins(settings.leader_app_origins):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forespørselen kom fra et sted som ikke har lov til å endre skiftlista.")
    if not CsrfTokenService(settings).tokens_match(
        request.cookies.get(CSRF_COOKIE_NAME), request.headers.get(CSRF_HEADER_NAME)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sikkerhetskoden mangler eller er utløpt. Last siden på nytt.")
