"""Bounded, fail-closed PostHog boolean flags for server-side switches."""

import time
from collections.abc import Awaitable, Callable

import httpx

from app.config import Settings

CACHE_SECONDS = 30
SPOTIFY_NOW_PLAYING = "personal-spotify-now-playing"


def build_flag(settings: Settings, key: str) -> Callable[[], Awaitable[bool]]:
    """Return a cached check of one flag; any failure or missing token is off."""
    enabled = False
    expires_at = 0.0
    refreshing = False

    token = settings.posthog_feature_flags_token or settings.posthog_project_token

    async def is_enabled() -> bool:
        nonlocal enabled, expires_at, refreshing
        if not token:
            return False
        if time.monotonic() < expires_at:
            return enabled
        if refreshing:
            return False
        refreshing = True
        enabled = False  # An expired value never stays on during an outage.
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.post(
                    f"{settings.posthog_host.rstrip('/')}/flags?v=2",
                    headers={"User-Agent": "posthog-python/7.0"},
                    json={
                        "api_key": token,
                        "distinct_id": "personal-runtime",
                        "person_properties": {"environment": settings.app_env},
                        "flag_keys_to_evaluate": [key],
                    },
                )
                response.raise_for_status()
                data = response.json()
                flag = data.get("flags", {}).get(key, {})
                enabled = (
                    not data.get("errorsWhileComputingFlags")
                    and flag.get("enabled") is True
                )
        except Exception:
            # Flag-service failures must never turn a switch on.
            enabled = False
        finally:
            expires_at = time.monotonic() + CACHE_SECONDS
            refreshing = False
        return enabled

    return is_enabled
