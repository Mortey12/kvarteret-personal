from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.auth.models import WebSession
from app.auth.roles import UserRole
from app.main import create_app
from app.runtime import build_application_container
from app.domain.spotify.now_playing import NowPlayingResult, SpotifyOAuthError
from tests.support.helpers import csrf_headers, make_authenticated_user, prime_csrf


class MiddlewareSessionStore:
    def __init__(self, user) -> None:
        self.user = user

    async def load_authenticated_user(self, session_id: str):
        return (
            WebSession(
                session_id=session_id,
                auth_user_id=self.user.auth_user_id,
                user_account_id=self.user.user_account_id,
                expires_at=datetime.now(UTC),
            ),
            self.user,
        )

    async def create_session(self, **kwargs):
        raise NotImplementedError

    async def delete_session(self, session_id: str) -> None:
        return None

    def invalidate_session_cache(self, session_id: str) -> None:
        return None


class FakeSpotifyNowPlayingService:
    def __init__(self) -> None:
        self.complete_callback_calls: list[dict[str, object]] = []
        self.clear_calls = 0
        self.raise_on_callback: Exception | None = None
        self.polling_enabled = True

    async def is_polling_enabled(self) -> bool:
        return self.polling_enabled

    async def get_state(self) -> NowPlayingResult:
        return NowPlayingResult(
            state={
                "authorized": True,
                "hasTrack": True,
                "isPlaybackActive": True,
                "name": "Paint It, Black",
                "artists": "The Rolling Stones",
                "album": "Aftermath",
                "image": "https://example.com/cover.jpg",
                "progressMs": 100,
                "durationMs": 200,
                "progressPercent": 50.0,
                "connectUrl": "https://personal.kvarteret.no/spotify/login",
            },
            cache_hit=False,
            cache_stale=False,
            cache_seconds=10.0,
        )

    async def has_shared_refresh_token(self) -> bool:
        return True

    def is_login_configured(self) -> bool:
        return True

    def build_authorize_url(self, *, session_id: str, user_account_id: int | None) -> str:
        return f"https://accounts.spotify.com/authorize?session={session_id}&user={user_account_id}"

    async def complete_oauth_callback(
        self,
        *,
        session_id: str,
        user_account_id: int | None,
        state: str,
        code: str,
    ) -> None:
        if self.raise_on_callback is not None:
            raise self.raise_on_callback
        self.complete_callback_calls.append(
            {
                "session_id": session_id,
                "user_account_id": user_account_id,
                "state": state,
                "code": code,
            }
        )

    async def clear_shared_token(self) -> None:
        self.clear_calls += 1


def _build_authed_client(
    user_role: UserRole,
    service: FakeSpotifyNowPlayingService | None = None,
) -> tuple[TestClient, str]:
    user = make_authenticated_user(user_role)
    container = build_application_container()
    container.session_store = MiddlewareSessionStore(user)
    container.now_playing_service = service or FakeSpotifyNowPlayingService()  # type: ignore[assignment]
    app = create_app(container=container)
    client = TestClient(app)
    session_cookie = container.session_cookie_signer.sign_session_id("session-123")
    return client, session_cookie


def test_spotify_now_playing_redirects_to_login_when_unauthenticated() -> None:
    client = TestClient(create_app())

    response = client.get("/spotify/now-playing", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_spotify_now_playing_rejects_non_admin_users() -> None:
    client, session_cookie = _build_authed_client(UserRole.VOLUNTEER)

    response = client.get(
        "/spotify/now-playing",
        cookies={"kvarteret_session": session_cookie},
    )

    assert response.status_code == 403


def test_spotify_now_playing_rejects_group_admin_users() -> None:
    client, session_cookie = _build_authed_client(UserRole.GROUP_ADMIN)

    response = client.get(
        "/spotify/now-playing",
        cookies={"kvarteret_session": session_cookie},
    )

    assert response.status_code == 403


def test_spotify_now_playing_page_renders_track_and_admin_actions() -> None:
    client, session_cookie = _build_authed_client(UserRole.ADMIN)

    response = client.get(
        "/spotify/now-playing",
        cookies={"kvarteret_session": session_cookie},
    )

    assert response.status_code == 200
    assert "Paint It, Black" in response.text
    assert "Koble til Spotify" in response.text
    assert "Koble fra" in response.text


def test_spotify_now_playing_page_banners_when_polling_disabled() -> None:
    service = FakeSpotifyNowPlayingService()
    service.polling_enabled = False
    client, session_cookie = _build_authed_client(UserRole.ADMIN, service)

    response = client.get(
        "/spotify/now-playing",
        cookies={"kvarteret_session": session_cookie},
    )

    assert response.status_code == 200
    assert "Spotify-polleringen er slått av" in response.text
    assert "personal-spotify-now-playing" in response.text


def test_spotify_login_redirects_to_spotify_authorize_url() -> None:
    service = FakeSpotifyNowPlayingService()
    client, session_cookie = _build_authed_client(UserRole.ADMIN, service)

    response = client.get(
        "/spotify/login",
        cookies={"kvarteret_session": session_cookie},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "https://accounts.spotify.com/authorize?session=session-123&user=5"


def test_spotify_callback_persists_token_and_redirects_with_success_message() -> None:
    service = FakeSpotifyNowPlayingService()
    client, session_cookie = _build_authed_client(UserRole.ADMIN, service)

    response = client.get(
        "/spotify/callback?state=test-state&code=test-code",
        cookies={"kvarteret_session": session_cookie},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Spotify+is+now+connected+for+shared+now-playing." in response.headers["location"]
    assert service.complete_callback_calls == [
        {
            "session_id": "session-123",
            "user_account_id": 5,
            "state": "test-state",
            "code": "test-code",
        }
    ]


def test_spotify_callback_redirects_with_error_message_when_callback_fails() -> None:
    service = FakeSpotifyNowPlayingService()
    service.raise_on_callback = SpotifyOAuthError("Spotify login state is invalid. Start the flow again.")
    client, session_cookie = _build_authed_client(UserRole.ADMIN, service)

    response = client.get(
        "/spotify/callback?state=bad-state&code=test-code",
        cookies={"kvarteret_session": session_cookie},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=Kunne+ikke+koble+til+Spotify.+Pr%C3%B8v+igjen." in response.headers["location"]


def test_spotify_logout_clears_shared_token_and_redirects() -> None:
    service = FakeSpotifyNowPlayingService()
    client, session_cookie = _build_authed_client(UserRole.ADMIN, service)
    prime_csrf(
        client,
        path="/spotify/now-playing",
        cookies={"kvarteret_session": session_cookie},
    )

    response = client.post(
        "/spotify/logout",
        headers=csrf_headers(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "message=Spotify+connection+cleared." in response.headers["location"]
    assert service.clear_calls == 1
