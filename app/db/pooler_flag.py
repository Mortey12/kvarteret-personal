"""Bounded, fail-closed PostHog remote config for infrastructure connections."""

import json
import time
from collections.abc import Awaitable, Callable

import httpx

from app.config import Settings

FLAG_KEY = "personal-database-transaction-pooler"
CACHE_SECONDS = 30


def build_percentage_provider(settings: Settings) -> Callable[[], Awaitable[int]]:
    percentage = 0
    expires_at = 0.0
    refreshing = False

    token = settings.posthog_flags_token

    async def get_percentage() -> int:
        nonlocal percentage, expires_at, refreshing
        if not settings.database_transaction_pooler_enabled or not token:
            return 0
        if time.monotonic() < expires_at:
            return percentage
        if refreshing:
            return 0
        refreshing = True
        percentage = 0  # Expired values never enable the trial during an outage.
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.post(
                    f"{settings.posthog_host.rstrip('/')}/flags?v=2",
                    headers={"User-Agent": "posthog-python/7.0"},
                    json={
                        "api_key": token,
                        "distinct_id": "personal-database-pooler-runtime",
                        "person_properties": {"environment": settings.app_env},
                        "flag_keys_to_evaluate": [FLAG_KEY],
                    },
                )
                response.raise_for_status()
                data = response.json()
                flag = data.get("flags", {}).get(FLAG_KEY, {})
                if not data.get("errorsWhileComputingFlags") and flag.get("enabled") is True:
                    payload = flag.get("metadata", {}).get("payload")
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    value = payload.get("percentage") if isinstance(payload, dict) else None
                    if type(value) is int and 0 <= value <= 100:
                        percentage = value
        except Exception:
            # Flag-service failures must never prevent a database connection.
            percentage = 0
        finally:
            expires_at = time.monotonic() + CACHE_SECONDS
            refreshing = False
        return percentage

    return get_percentage
