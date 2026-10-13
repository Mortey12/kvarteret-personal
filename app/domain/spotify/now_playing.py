from __future__ import annotations

from app.observability import get_logger

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import token_urlsafe
from time import monotonic
from collections.abc import Awaitable
from typing import Any, Callable
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.feature_flags import SPOTIFY_NOW_PLAYING, build_flag
from app.errors import NotConfiguredError
from app.domain.spotify.repository import IntegrationTokensRepository


_JSON_CONTENT_TYPE = "application/json"
logger = get_logger(__name__)

_SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SPOTIFY_CURRENTLY_PLAYING_URL = (
    "https://api.spotify.com/v1/me/player/currently-playing"
)
_SPOTIFY_PROVIDER = "spotify"
_SPOTIFY_SCOPES = "user-read-currently-playing user-read-playback-state"
_STATE_MAX_AGE_SECONDS = 600
_FAILURE_BACKOFF_SECONDS = 60.0


class SpotifyOAuthError(RuntimeError):
    """Raised when the shared Spotify OAuth flow cannot be completed."""


class SpotifyOAuthStateError(SpotifyOAuthError):
    """Raised when the Spotify OAuth callback state is invalid."""


@dataclass(frozen=True, slots=True)
class NowPlayingResult:
    state: dict[str, object]
    cache_hit: bool
    cache_stale: bool
    cache_seconds: float


@dataclass(slots=True)
class _CacheEntry:
    state: dict[str, object]
    fetched_at: float


class NowPlayingService:
    def __init__(
        self,
        settings: Settings,
        repository: IntegrationTokensRepository,
        client: httpx.AsyncClient | None = None,
        now_fn: Callable[[], float] | None = None,
        polling_enabled: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.settings = settings
        # PostHog flag `personal-spotify-now-playing`; off when unreachable.
        self._polling_enabled = polling_enabled or build_flag(
            settings, SPOTIFY_NOW_PLAYING
        )
        self.repository = repository
        self._client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        self._owns_client = client is None
        self._now_fn = now_fn or monotonic
        self._cache_lock = asyncio.Lock()
        self._cache_entry: _CacheEntry | None = None
        self._refresh_token_cache: str | None = None
        self._refresh_token_cache_loaded = False
        self._failure_backoff_until = 0.0
        self._failure_logged = False
        self._state_serializer = URLSafeTimedSerializer(
            settings.app_secret_key, salt="kvarteret-spotify-oauth"
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_state(self) -> NowPlayingResult:
        cache_seconds = self.settings.now_playing_cache_seconds
        if not await self._polling_enabled():
            # Spotify now-playing polling is switched off. Do not touch
            # the repository or the Spotify API so the recurring upstream 4xx
            # errors cannot recur or spam error tracking.
            return self._polling_disabled_result(cache_seconds)
        stale_grace_seconds = self.settings.now_playing_stale_grace_seconds
        refresh_token = await self._get_refresh_token()
        if not self._has_oauth_client_config() or not refresh_token:
            return NowPlayingResult(
                state=self._default_state(authorized=False),
                cache_hit=False,
                cache_stale=False,
                cache_seconds=cache_seconds,
            )

        if self._now_fn() < self._failure_backoff_until:
            return self._failure_fallback(cache_seconds)

        try:
            return await self._get_state_with_cache(
                refresh_token=refresh_token,
                cache_seconds=cache_seconds,
                stale_grace_seconds=stale_grace_seconds,
            )
        except Exception:
            self._record_fetch_failure()
            return self._failure_fallback(cache_seconds)

    def build_authorize_url(
        self, *, session_id: str, user_account_id: int | None
    ) -> str:
        if not self._is_login_configured():
            raise NotConfiguredError("Spotify OAuth is not configured.")
        params = {
            "client_id": self.settings.spotify_client_id or "",
            "response_type": "code",
            "redirect_uri": self._redirect_uri(),
            "scope": _SPOTIFY_SCOPES,
            "state": self._state_serializer.dumps(
                {
                    "session_id": session_id,
                    "user_account_id": user_account_id,
                    "nonce": token_urlsafe(16),
                }
            ),
        }
        return f"{_SPOTIFY_AUTHORIZE_URL}?{urlencode(params)}"

    async def complete_oauth_callback(
        self,
        *,
        session_id: str,
        user_account_id: int | None,
        state: str,
        code: str,
    ) -> None:
        if not self._is_login_configured():
            raise NotConfiguredError("Spotify OAuth is not configured.")
        payload = self._validate_state(
            state=state, session_id=session_id, user_account_id=user_account_id
        )
        if (
            payload.get("session_id") != session_id
            or payload.get("user_account_id") != user_account_id
        ):
            raise SpotifyOAuthStateError(
                "Spotify login state does not match the active admin session."
            )

        token_payload = await self._exchange_authorization_code(code)
        refresh_token = (
            _str_or_none(token_payload.get("refresh_token"))
            or await self._get_refresh_token()
        )
        if not refresh_token:
            raise SpotifyOAuthError(
                "Spotify authorization response did not include a refresh token."
            )

        await self._save_refresh_token(
            refresh_token, updated_by_user_account_id=user_account_id
        )
        self._refresh_token_cache = refresh_token
        self._cache_entry = None
        self._clear_fetch_failure()

    async def clear_shared_token(self) -> None:
        try:
            await self.repository.delete_token(_SPOTIFY_PROVIDER)
        except SQLAlchemyError as exc:
            logger.warning("spotify.connection.delete_failed", exc_info=exc, extra={"event": "spotify.connection.delete_failed"})
            raise SpotifyOAuthError(
                "Spotify token storage is unavailable. Apply the latest database migration."
            ) from exc
        self._refresh_token_cache = None
        self._cache_entry = None
        self._clear_fetch_failure()

    async def has_shared_refresh_token(self) -> bool:
        return bool(await self._get_refresh_token())

    def is_login_configured(self) -> bool:
        return self._is_login_configured()

    async def is_polling_enabled(self) -> bool:
        return await self._polling_enabled()

    async def _get_state_with_cache(
        self,
        *,
        refresh_token: str,
        cache_seconds: float,
        stale_grace_seconds: float,
    ) -> NowPlayingResult:
        now = self._now_fn()
        entry = self._cache_entry
        if entry and _cache_age_seconds(now, entry) <= cache_seconds:
            return _result(
                entry, cache_hit=True, cache_stale=False, cache_seconds=cache_seconds
            )

        if self._cache_lock.locked():
            if (
                entry
                and _cache_age_seconds(now, entry)
                <= cache_seconds + stale_grace_seconds
            ):
                return _result(
                    entry, cache_hit=True, cache_stale=True, cache_seconds=cache_seconds
                )
            async with self._cache_lock:
                return await self._refresh_locked(
                    refresh_token=refresh_token,
                    cache_seconds=cache_seconds,
                    stale_grace_seconds=stale_grace_seconds,
                )

        async with self._cache_lock:
            return await self._refresh_locked(
                refresh_token=refresh_token,
                cache_seconds=cache_seconds,
                stale_grace_seconds=stale_grace_seconds,
            )

    async def _refresh_locked(
        self,
        *,
        refresh_token: str,
        cache_seconds: float,
        stale_grace_seconds: float,
    ) -> NowPlayingResult:
        now = self._now_fn()
        entry = self._cache_entry
        if entry and _cache_age_seconds(now, entry) <= cache_seconds:
            return _result(
                entry, cache_hit=True, cache_stale=False, cache_seconds=cache_seconds
            )

        try:
            fresh_state = await self._fetch_state_uncached(refresh_token)
        except Exception:
            self._record_fetch_failure()
            if (
                entry
                and _cache_age_seconds(now, entry)
                <= cache_seconds + stale_grace_seconds
            ):
                return _result(
                    entry, cache_hit=True, cache_stale=True, cache_seconds=cache_seconds
                )
            raise

        self._clear_fetch_failure()
        self._cache_entry = _CacheEntry(
            state=dict(fresh_state), fetched_at=self._now_fn()
        )
        return NowPlayingResult(
            state=dict(fresh_state),
            cache_hit=False,
            cache_stale=False,
            cache_seconds=cache_seconds,
        )

    async def _fetch_state_uncached(self, refresh_token: str) -> dict[str, object]:
        access_token, rotated_refresh_token = await self._refresh_access_token(
            refresh_token
        )
        if rotated_refresh_token and rotated_refresh_token != refresh_token:
            await self._save_refresh_token(
                rotated_refresh_token, updated_by_user_account_id=None
            )
            self._refresh_token_cache = rotated_refresh_token

        response = await self._client.get(
            _SPOTIFY_CURRENTLY_PLAYING_URL,
            headers={
                "Accept": _JSON_CONTENT_TYPE,
                "Authorization": f"Bearer {access_token}",
            },
        )

        if response.status_code == 204:
            return self._default_state(authorized=True)
        response.raise_for_status()

        payload = response.json()
        item = payload.get("item")
        if not isinstance(item, dict):
            return self._default_state(authorized=True)

        progress_ms = _int_or_none(payload.get("progress_ms"))
        duration_ms = _int_or_none(item.get("duration_ms"))
        progress_percent = None
        if progress_ms is not None and duration_ms and duration_ms > 0:
            progress_percent = round((progress_ms / duration_ms) * 100, 1)

        album = item.get("album") if isinstance(item.get("album"), dict) else {}
        images = album.get("images") if isinstance(album.get("images"), list) else []
        image = None
        if images:
            first_image = images[0]
            if isinstance(first_image, dict):
                image = _str_or_none(first_image.get("url"))

        artists = ", ".join(
            artist_name
            for artist in item.get("artists", [])
            if isinstance(artist, dict)
            and (artist_name := _str_or_none(artist.get("name")))
        )

        return {
            **self._default_state(authorized=True),
            "hasTrack": True,
            "isPlaybackActive": bool(payload.get("is_playing", False)),
            "name": _str_or_none(item.get("name")),
            "artists": artists or None,
            "album": _str_or_none(album.get("name")),
            "image": image,
            "progressMs": progress_ms,
            "durationMs": duration_ms,
            "progressPercent": progress_percent,
        }

    async def _get_refresh_token(self) -> str | None:
        if self._refresh_token_cache_loaded:
            return self._refresh_token_cache

        try:
            stored_token = await self.repository.get_token(_SPOTIFY_PROVIDER)
        except SQLAlchemyError as exc:
            logger.warning("spotify.connection.load_failed", exc_info=exc, extra={"event": "spotify.connection.load_failed"})
            stored_token = None
        if stored_token is not None:
            self._refresh_token_cache = stored_token.refresh_token
            self._refresh_token_cache_loaded = True
            return stored_token.refresh_token

        fallback_token = (self.settings.spotify_refresh_token or "").strip() or None
        self._refresh_token_cache = fallback_token
        self._refresh_token_cache_loaded = True
        return fallback_token

    async def _save_refresh_token(
        self, refresh_token: str, *, updated_by_user_account_id: int | None
    ) -> None:
        try:
            await self.repository.save_token(
                provider=_SPOTIFY_PROVIDER,
                refresh_token=refresh_token,
                updated_at=datetime.now(UTC),
                updated_by_user_account_id=updated_by_user_account_id,
            )
        except SQLAlchemyError as exc:
            logger.warning("spotify.connection.save_failed", exc_info=exc, extra={"event": "spotify.connection.save_failed"})
            raise SpotifyOAuthError(
                "Spotify token storage is unavailable. Apply the latest database migration."
            ) from exc

    async def _refresh_access_token(self, refresh_token: str) -> tuple[str, str | None]:
        response = await self._client.post(
            _SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(
                self.settings.spotify_client_id or "",
                self.settings.spotify_client_secret or "",
            ),
            headers={"Accept": _JSON_CONTENT_TYPE},
        )
        response.raise_for_status()
        payload = response.json()
        access_token = _str_or_none(payload.get("access_token"))
        if not access_token:
            raise SpotifyOAuthError(
                "Spotify token refresh response lacked access_token."
            )
        return access_token, _str_or_none(payload.get("refresh_token"))

    async def _exchange_authorization_code(self, code: str) -> dict[str, Any]:
        response = await self._client.post(
            _SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri(),
            },
            auth=(
                self.settings.spotify_client_id or "",
                self.settings.spotify_client_secret or "",
            ),
            headers={"Accept": _JSON_CONTENT_TYPE},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise SpotifyOAuthError("Spotify authorization response was invalid.")
        return payload

    def _validate_state(
        self, *, state: str, session_id: str, user_account_id: int | None
    ) -> dict[str, Any]:
        try:
            payload = self._state_serializer.loads(
                state, max_age=_STATE_MAX_AGE_SECONDS
            )
        except SignatureExpired as exc:
            raise SpotifyOAuthStateError(
                "Spotify login state has expired. Start the flow again."
            ) from exc
        except BadSignature as exc:
            raise SpotifyOAuthStateError(
                "Spotify login state is invalid. Start the flow again."
            ) from exc
        if not isinstance(payload, dict):
            raise SpotifyOAuthStateError(
                "Spotify login state is invalid. Start the flow again."
            )
        return payload

    def _default_state(self, *, authorized: bool) -> dict[str, object]:
        return {
            "authorized": authorized,
            "hasTrack": False,
            "isPlaybackActive": False,
            "name": None,
            "artists": None,
            "album": None,
            "image": None,
            "progressMs": None,
            "durationMs": None,
            "progressPercent": None,
            "connectUrl": self._connect_url(),
        }

    def _record_fetch_failure(self) -> None:
        self._failure_backoff_until = self._now_fn() + _FAILURE_BACKOFF_SECONDS
        if not self._failure_logged:
            logger.exception("spotify.now_playing.fetch_failed", extra={"event": "spotify.now_playing.fetch_failed"})
            self._failure_logged = True

    def _clear_fetch_failure(self) -> None:
        self._failure_backoff_until = 0.0
        self._failure_logged = False

    def _failure_fallback(self, cache_seconds: float) -> NowPlayingResult:
        if self._cache_entry is not None:
            return _result(
                self._cache_entry,
                cache_hit=True,
                cache_stale=True,
                cache_seconds=cache_seconds,
            )
        return NowPlayingResult(
            state=self._default_state(authorized=True),
            cache_hit=False,
            cache_stale=False,
            cache_seconds=cache_seconds,
        )

    def _polling_disabled_result(self, cache_seconds: float) -> NowPlayingResult:
        # Report the account as connected-but-idle so consumers do not prompt
        # for a reconnect while the upstream polling issue is being resolved.
        return NowPlayingResult(
            state=self._default_state(authorized=True),
            cache_hit=False,
            cache_stale=False,
            cache_seconds=cache_seconds,
        )

    def _connect_url(self) -> str:
        public_base_url = (self.settings.app_public_base_url or "").strip().rstrip("/")
        if public_base_url:
            return f"{public_base_url}/spotify/login"
        return ""

    def _redirect_uri(self) -> str:
        public_base_url = (self.settings.app_public_base_url or "").strip().rstrip("/")
        if not public_base_url:
            raise NotConfiguredError(
                "APP_PUBLIC_BASE_URL must be configured for Spotify OAuth."
            )
        return f"{public_base_url}/spotify/callback"

    def _has_oauth_client_config(self) -> bool:
        return bool(
            self.settings.spotify_client_id and self.settings.spotify_client_secret
        )

    def _is_login_configured(self) -> bool:
        return self._has_oauth_client_config() and bool(
            (self.settings.app_public_base_url or "").strip()
        )


def _cache_age_seconds(now: float, entry: _CacheEntry) -> float:
    return max(0.0, now - entry.fetched_at)


def _result(
    entry: _CacheEntry, *, cache_hit: bool, cache_stale: bool, cache_seconds: float
) -> NowPlayingResult:
    return NowPlayingResult(
        state=dict(entry.state),
        cache_hit=cache_hit,
        cache_stale=cache_stale,
        cache_seconds=cache_seconds,
    )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
