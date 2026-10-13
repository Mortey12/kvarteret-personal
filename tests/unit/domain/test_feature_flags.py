from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.feature_flags import SPOTIFY_NOW_PLAYING, build_flag


def _response(body: dict) -> httpx.Response:
    return httpx.Response(200, request=httpx.Request("POST", "https://example.test"), json=body)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"flags": {SPOTIFY_NOW_PLAYING: {"enabled": True}}}, True),
        ({"flags": {SPOTIFY_NOW_PLAYING: {"enabled": False}}}, False),
        ({"flags": {}}, False),
        ({"flags": {SPOTIFY_NOW_PLAYING: {"enabled": True}}, "errorsWhileComputingFlags": True}, False),
    ],
)
async def test_flag_value_is_cached(monkeypatch, body, expected):
    post = AsyncMock(return_value=_response(body))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    flag = build_flag(Settings(_env_file=None, posthog_project_token="test"), SPOTIFY_NOW_PLAYING)
    assert await flag() is expected
    assert await flag() is expected
    assert post.await_count == 1


async def test_expired_value_fails_closed_and_recovers(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.feature_flags.time.monotonic", lambda: clock[0])
    response = _response({"flags": {SPOTIFY_NOW_PLAYING: {"enabled": True}}})
    post = AsyncMock(side_effect=[response, TimeoutError(), response])
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    flag = build_flag(Settings(_env_file=None, posthog_project_token="test"), SPOTIFY_NOW_PLAYING)
    assert await flag() is True
    clock[0] += 31
    assert await flag() is False
    clock[0] += 31
    assert await flag() is True


async def test_missing_token_is_off_without_calling_posthog(monkeypatch):
    post = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    settings = Settings(_env_file=None).model_copy(
        update={"posthog_project_token": None, "posthog_feature_flags_token": None}
    )
    assert await build_flag(settings, SPOTIFY_NOW_PLAYING)() is False
    post.assert_not_called()
