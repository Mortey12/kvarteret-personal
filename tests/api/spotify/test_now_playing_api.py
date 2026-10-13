from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_now_playing_service
from app.main import create_app
from app.domain.spotify.now_playing import NowPlayingResult, NowPlayingService, SpotifyOAuthStateError


def _flag(value: bool):
    async def is_enabled() -> bool:
        return value

    return is_enabled


class FakeNowPlayingService:
    async def get_state(self) -> NowPlayingResult:
        return NowPlayingResult(
            state={
                "authorized": True,
                "hasTrack": True,
                "isPlaybackActive": True,
                "name": "Track",
                "artists": "Artist One, Artist Two",
                "album": "Album",
                "image": "https://example.com/cover.jpg",
                "progressMs": 1500,
                "durationMs": 3000,
                "progressPercent": 50.0,
                "connectUrl": "https://personal.kvarteret.no/spotify/login",
            },
            cache_hit=True,
            cache_stale=False,
            cache_seconds=10.0,
        )


@dataclass
class FakeIntegrationToken:
    provider: str
    refresh_token: str
    updated_at: datetime
    updated_by_user_account_id: int | None


class FakeIntegrationTokensRepository:
    def __init__(self, token: FakeIntegrationToken | None = None) -> None:
        self.token = token
        self.saved: list[FakeIntegrationToken] = []
        self.deleted_providers: list[str] = []

    async def get_token(self, provider: str) -> FakeIntegrationToken | None:
        if self.token and self.token.provider == provider:
            return self.token
        return None

    async def save_token(
        self,
        *,
        provider: str,
        refresh_token: str,
        updated_at: datetime,
        updated_by_user_account_id: int | None,
    ) -> None:
        self.token = FakeIntegrationToken(provider, refresh_token, updated_at, updated_by_user_account_id)
        self.saved.append(self.token)

    async def delete_token(self, provider: str) -> None:
        self.deleted_providers.append(provider)
        if self.token and self.token.provider == provider:
            self.token = None


def test_now_playing_api_returns_public_json_with_cache_headers() -> None:
    app = create_app()
    app.dependency_overrides[get_now_playing_service] = lambda: FakeNowPlayingService()
    client = TestClient(app)

    response = client.get("/api/now-playing")

    assert response.status_code == 200
    assert response.json()["hasTrack"] is True
    assert response.json()["connectUrl"] == "https://personal.kvarteret.no/spotify/login"
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["X-Now-Playing-Cache-Seconds"] == "10.0"
    assert response.headers["X-Now-Playing-Cache-Hit"] == "1"
    assert response.headers["X-Now-Playing-Cache-Stale"] == "0"


@pytest.mark.asyncio
async def test_now_playing_service_prefers_database_refresh_token_over_env_fallback() -> None:
    requests: list[tuple[str, str, str | None]] = []
    repository = FakeIntegrationTokensRepository(
        token=FakeIntegrationToken(
            provider="spotify",
            refresh_token="database-refresh-token",
            updated_at=datetime.now(UTC),
            updated_by_user_account_id=5,
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        requests.append((request.method, str(request.url), body))
        if str(request.url) == "https://accounts.spotify.com/api/token":
            return httpx.Response(200, json={"access_token": "spotify-access-token"})
        if str(request.url) == "https://api.spotify.com/v1/me/player/currently-playing":
            return httpx.Response(
                200,
                json={
                    "is_playing": True,
                    "progress_ms": 1500,
                    "item": {
                        "name": "Track",
                        "duration_ms": 3000,
                        "artists": [{"name": "Artist One"}, {"name": "Artist Two"}],
                        "album": {
                            "name": "Album",
                            "images": [{"url": "https://example.com/cover.jpg"}],
                        },
                    },
                },
            )
        raise AssertionError(f"Unexpected request to {request.url}")

    service = NowPlayingService(
        Settings(
            app_public_base_url="https://personal.kvarteret.no",
            spotify_client_id="spotify-client-id",
            spotify_client_secret="spotify-client-secret",
            spotify_refresh_token="env-refresh-token",
        ),
        repository=repository,  # type: ignore[arg-type]
        polling_enabled=_flag(True),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await service.get_state()
    await service.aclose()

    assert requests[0][0:2] == ("POST", "https://accounts.spotify.com/api/token")
    assert requests[0][2] is not None
    assert "refresh_token=database-refresh-token" in requests[0][2]
    assert result.state == {
        "authorized": True,
        "hasTrack": True,
        "isPlaybackActive": True,
        "name": "Track",
        "artists": "Artist One, Artist Two",
        "album": "Album",
        "image": "https://example.com/cover.jpg",
        "progressMs": 1500,
        "durationMs": 3000,
        "progressPercent": 50.0,
        "connectUrl": "https://personal.kvarteret.no/spotify/login",
    }


@pytest.mark.asyncio
async def test_now_playing_service_returns_unconfigured_state_without_any_refresh_token() -> None:
    service = NowPlayingService(
        Settings(
            app_public_base_url="https://personal.kvarteret.no",
            spotify_client_id="spotify-client-id",
            spotify_client_secret="spotify-client-secret",
        ),
        repository=FakeIntegrationTokensRepository(),  # type: ignore[arg-type]
        polling_enabled=_flag(True),
    )

    result = await service.get_state()
    await service.aclose()

    assert result.state == {
        "authorized": False,
        "hasTrack": False,
        "isPlaybackActive": False,
        "name": None,
        "artists": None,
        "album": None,
        "image": None,
        "progressMs": None,
        "durationMs": None,
        "progressPercent": None,
        "connectUrl": "https://personal.kvarteret.no/spotify/login",
    }


@pytest.mark.asyncio
async def test_now_playing_service_polling_disabled_skips_spotify_and_repository() -> None:
    class ExplodingRepository:
        async def get_token(self, provider: str) -> FakeIntegrationToken | None:
            raise AssertionError("repository must not be read while polling is disabled")

        async def save_token(self, **kwargs) -> None:
            raise AssertionError("repository must not be written while polling is disabled")

        async def delete_token(self, provider: str) -> None:
            raise AssertionError("repository must not be written while polling is disabled")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            f"Spotify must not be contacted while polling is disabled: {request.url}"
        )

    service = NowPlayingService(
        Settings(
            app_public_base_url="https://personal.kvarteret.no",
            spotify_client_id="spotify-client-id",
            spotify_client_secret="spotify-client-secret",
        ),
        repository=ExplodingRepository(),  # type: ignore[arg-type]
        polling_enabled=_flag(False),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await service.get_state()
    await service.aclose()

    assert await service.is_polling_enabled() is False
    assert result.cache_hit is False
    assert result.cache_stale is False
    assert result.state == {
        "authorized": True,
        "hasTrack": False,
        "isPlaybackActive": False,
        "name": None,
        "artists": None,
        "album": None,
        "image": None,
        "progressMs": None,
        "durationMs": None,
        "progressPercent": None,
        "connectUrl": "https://personal.kvarteret.no/spotify/login",
    }


@pytest.mark.asyncio
async def test_now_playing_service_serves_stale_snapshot_after_refresh_failure() -> None:
    clock = {"now": 0.0}
    repository = FakeIntegrationTokensRepository(
        token=FakeIntegrationToken("spotify", "database-refresh-token", datetime.now(UTC), 5)
    )
    responses = iter(
        [
            httpx.Response(200, json={"access_token": "spotify-access-token"}),
            httpx.Response(
                200,
                json={
                    "is_playing": True,
                    "progress_ms": 1000,
                    "item": {
                        "name": "Track",
                        "duration_ms": 3000,
                        "artists": [{"name": "Artist"}],
                        "album": {"name": "Album", "images": []},
                    },
                },
            ),
            httpx.Response(500, json={"error": "server_error"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    service = NowPlayingService(
        Settings(
            app_public_base_url="https://personal.kvarteret.no",
            spotify_client_id="spotify-client-id",
            spotify_client_secret="spotify-client-secret",
            now_playing_cache_seconds=10.0,
            now_playing_stale_grace_seconds=30.0,
        ),
        repository=repository,  # type: ignore[arg-type]
        polling_enabled=_flag(True),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        now_fn=lambda: clock["now"],
    )

    first = await service.get_state()
    clock["now"] = 15.0
    second = await service.get_state()
    await service.aclose()

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.cache_stale is True
    assert second.state["name"] == "Track"


@pytest.mark.asyncio
async def test_now_playing_service_backs_off_and_logs_once_per_outage(caplog) -> None:
    clock = {"now": 0.0}
    request_count = 0
    repository = FakeIntegrationTokensRepository(
        token=FakeIntegrationToken(
            "spotify", "database-refresh-token", datetime.now(UTC), 5
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500, json={"error": "server_error"})

    service = NowPlayingService(
        Settings(
            app_public_base_url="https://personal.kvarteret.no",
            spotify_client_id="spotify-client-id",
            spotify_client_secret="spotify-client-secret",
        ),
        repository=repository,  # type: ignore[arg-type]
        polling_enabled=_flag(True),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        now_fn=lambda: clock["now"],
    )

    with caplog.at_level(logging.ERROR):
        await service.get_state()
        clock["now"] = 30.0
        await service.get_state()
        clock["now"] = 61.0
        await service.get_state()
    await service.aclose()

    assert request_count == 2
    assert sum(
        getattr(record, "event", None) == "spotify.now_playing.fetch_failed"
        for record in caplog.records
    ) == 1


@pytest.mark.asyncio
async def test_now_playing_service_builds_authorize_url_for_new_callback() -> None:
    service = NowPlayingService(
        Settings(
            app_public_base_url="https://personal.kvarteret.no",
            spotify_client_id="spotify-client-id",
            spotify_client_secret="spotify-client-secret",
        ),
        repository=FakeIntegrationTokensRepository(),  # type: ignore[arg-type]
        polling_enabled=_flag(True),
    )

    authorize_url = service.build_authorize_url(session_id="session-123", user_account_id=5)
    await service.aclose()
    parsed = urlparse(authorize_url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.spotify.com"
    assert parsed.path == "/authorize"
    assert params["client_id"] == ["spotify-client-id"]
    assert params["redirect_uri"] == ["https://personal.kvarteret.no/spotify/callback"]
    assert params["scope"] == ["user-read-currently-playing user-read-playback-state"]
    assert "state" in params


@pytest.mark.asyncio
async def test_now_playing_service_persists_refresh_token_after_callback() -> None:
    repository = FakeIntegrationTokensRepository()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://accounts.spotify.com/api/token"
        return httpx.Response(200, json={"access_token": "spotify-access-token", "refresh_token": "new-refresh-token"})

    service = NowPlayingService(
        Settings(
            app_public_base_url="https://personal.kvarteret.no",
            spotify_client_id="spotify-client-id",
            spotify_client_secret="spotify-client-secret",
        ),
        repository=repository,  # type: ignore[arg-type]
        polling_enabled=_flag(True),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    authorize_url = service.build_authorize_url(session_id="session-123", user_account_id=5)
    state = parse_qs(urlparse(authorize_url).query)["state"][0]
    await service.complete_oauth_callback(
        session_id="session-123",
        user_account_id=5,
        state=state,
        code="spotify-code",
    )
    await service.aclose()

    assert repository.token is not None
    assert repository.token.refresh_token == "new-refresh-token"
    assert repository.token.updated_by_user_account_id == 5


@pytest.mark.asyncio
async def test_now_playing_service_rejects_callback_for_different_admin_session() -> None:
    service = NowPlayingService(
        Settings(
            app_public_base_url="https://personal.kvarteret.no",
            spotify_client_id="spotify-client-id",
            spotify_client_secret="spotify-client-secret",
        ),
        repository=FakeIntegrationTokensRepository(),  # type: ignore[arg-type]
        polling_enabled=_flag(True),
    )

    authorize_url = service.build_authorize_url(session_id="session-123", user_account_id=5)
    state = parse_qs(urlparse(authorize_url).query)["state"][0]

    with pytest.raises(SpotifyOAuthStateError):
        await service.complete_oauth_callback(
            session_id="other-session",
            user_account_id=5,
            state=state,
            code="spotify-code",
        )

    await service.aclose()
