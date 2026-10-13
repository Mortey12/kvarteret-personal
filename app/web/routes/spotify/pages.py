from __future__ import annotations

from app.observability import get_logger

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse

from app.auth.models import AuthenticatedUser, WebSession
from app.dependencies import get_now_playing_service, require_admin_user
from app.errors import NotConfiguredError
from app.observability import log_admin_activity
from app.domain.spotify.now_playing import NowPlayingService, SpotifyOAuthError
from app.web.templates import templates

_SPOTIFY_OAUTH_LOGIN_CALLBACK = "spotify.oauth.login.callback"

router = APIRouter()
logger = get_logger(__name__)


@router.get("/spotify/now-playing")
async def spotify_now_playing_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    current_user: AuthenticatedUser = Depends(require_admin_user),
    now_playing_service: NowPlayingService = Depends(get_now_playing_service),
):
    now_playing = await now_playing_service.get_state()
    has_shared_token = await now_playing_service.has_shared_refresh_token()
    log_admin_activity(
        request=request,
        user=current_user,
        action="spotify.now_playing.view",
        subject_type="spotify",
        details={
            "authorized": now_playing.state["authorized"],
            "has_track": now_playing.state["hasTrack"],
        },
    )
    return templates.TemplateResponse(
        request,
        "pages/spotify/spotify_now_playing.html",
        {
            "title": "Spotify Now Playing",
            "section": "spotify",
            "current_user": current_user,
            "message": message,
            "error_message": error,
            "now_playing": now_playing.state,
            "is_spotify_configured": now_playing_service.is_login_configured(),
            "has_shared_token": has_shared_token,
            "spotify_polling_enabled": await now_playing_service.is_polling_enabled(),
        },
    )


@router.get("/spotify/login")
async def spotify_login(
    request: Request,
    current_user: AuthenticatedUser = Depends(require_admin_user),
    now_playing_service: NowPlayingService = Depends(get_now_playing_service),
):
    session = _require_active_session(request)
    try:
        authorize_url = now_playing_service.build_authorize_url(
            session_id=session.session_id,
            user_account_id=current_user.user_account_id,
        )
    except NotConfiguredError:
        logger.exception("spotify.authorization.unconfigured", extra={"event": "spotify.authorization.unconfigured"})
        return _redirect_with_feedback(error="Spotify er ikke konfigurert.")
    log_admin_activity(
        request=request,
        user=current_user,
        action="spotify.oauth.login.start",
        subject_type="spotify",
        subject_id=session.session_id,
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/spotify/callback")
async def spotify_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    current_user: AuthenticatedUser = Depends(require_admin_user),
    now_playing_service: NowPlayingService = Depends(get_now_playing_service),
):
    session = _require_active_session(request)
    if error:
        log_admin_activity(
            request=request,
            user=current_user,
            action=_SPOTIFY_OAUTH_LOGIN_CALLBACK,
            outcome="failure",
            subject_type="spotify",
            subject_id=session.session_id,
            details={"spotify_error": error},
        )
        return _redirect_with_feedback(error="Spotify avviste autoriseringen.")
    if not code or not state:
        return _redirect_with_feedback(
            error="Spotify callback was incomplete. Start the flow again."
        )

    try:
        await now_playing_service.complete_oauth_callback(
            session_id=session.session_id,
            user_account_id=current_user.user_account_id,
            state=state,
            code=code,
        )
    except (NotConfiguredError, SpotifyOAuthError) as exc:
        logger.exception("spotify.authorization.failed", extra={"event": "spotify.authorization.failed"})
        log_admin_activity(
            request=request,
            user=current_user,
            action=_SPOTIFY_OAUTH_LOGIN_CALLBACK,
            outcome="failure",
            subject_type="spotify",
            subject_id=session.session_id,
            details={"reason": str(exc)},
        )
        return _redirect_with_feedback(
            error="Kunne ikke koble til Spotify. Prøv igjen."
        )

    log_admin_activity(
        request=request,
        user=current_user,
        action=_SPOTIFY_OAUTH_LOGIN_CALLBACK,
        subject_type="spotify",
        subject_id=session.session_id,
    )
    return _redirect_with_feedback(
        message="Spotify is now connected for shared now-playing."
    )


@router.post("/spotify/logout")
async def spotify_logout(
    request: Request,
    current_user: AuthenticatedUser = Depends(require_admin_user),
    now_playing_service: NowPlayingService = Depends(get_now_playing_service),
):
    try:
        await now_playing_service.clear_shared_token()
    except SpotifyOAuthError:
        logger.exception("spotify.disconnection.failed", extra={"event": "spotify.disconnection.failed"})
        return _redirect_with_feedback(error="Kunne ikke koble fra Spotify akkurat nå.")
    log_admin_activity(
        request=request,
        user=current_user,
        action="spotify.oauth.logout",
        subject_type="spotify",
    )
    return _redirect_with_feedback(message="Spotify connection cleared.")


def _require_active_session(request: Request) -> WebSession:
    session = getattr(request.state, "session", None)
    if session is None:
        msg = "An active admin session is required for Spotify OAuth."
        raise SpotifyOAuthError(msg)
    return session


def _redirect_with_feedback(
    *, message: str | None = None, error: str | None = None
) -> RedirectResponse:
    query = urlencode(
        {
            key: value
            for key, value in {"message": message, "error": error}.items()
            if value
        }
    )
    destination = "/spotify/now-playing"
    if query:
        destination = f"{destination}?{query}"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)
