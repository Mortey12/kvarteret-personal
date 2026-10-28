"""POSTHOG_ENABLED=false (hosted demo): nothing at all is sent to PostHog."""

from __future__ import annotations

import logging
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

import app.domain.agent_chat.service as agent_chat_module
import app.error_tracking as error_tracking_module
import app.telemetry as telemetry_module
from app.config import Settings, validate_production_secrets
from app.db.pooler_flag import build_percentage_provider
from app.domain.agent_chat.service import AgentChatService
from app.feature_flags import SPOTIFY_NOW_PLAYING, build_flag
from app.main import create_app
from app.runtime import build_application_container


def _production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "app_secret_key": "s" * 40,
        "personal_signing_secret": "p" * 40,
        "app_public_base_url": "https://demo.example.dev",
        "email_dispatch_enabled": False,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "posthog_enabled": False,
    }
    values.update(overrides)
    return validate_production_secrets(Settings(_env_file=None, **values))


@pytest.fixture
def posthog_spies(monkeypatch):
    """Replace every PostHog client and exporter, and refuse any socket."""
    spies = {
        "span_exporter": MagicMock(),
        "log_exporter": MagicMock(),
        "error_tracking_client": MagicMock(),
        "agent_chat_client": MagicMock(),
        "flags_post": AsyncMock(),
        "connect": MagicMock(side_effect=OSError("network is off in this test")),
    }
    monkeypatch.setattr(telemetry_module, "OTLPSpanExporter", spies["span_exporter"])
    monkeypatch.setattr(telemetry_module, "OTLPLogExporter", spies["log_exporter"])
    monkeypatch.setattr(error_tracking_module, "Posthog", spies["error_tracking_client"])
    monkeypatch.setattr(agent_chat_module, "Posthog", spies["agent_chat_client"])
    monkeypatch.setattr(httpx.AsyncClient, "post", spies["flags_post"])
    monkeypatch.setattr(socket.socket, "connect", spies["connect"])
    return spies


def _assert_no_posthog_call(spies) -> None:
    for name, spy in spies.items():
        assert not spy.called, f"{name} was called"


def test_posthog_enabled_reads_false_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("POSTHOG_ENABLED", "false")
    assert Settings(_env_file=None).posthog_enabled is False
    monkeypatch.delenv("POSTHOG_ENABLED")
    assert Settings(_env_file=None).posthog_enabled is True


def test_posthog_is_on_in_production_by_default() -> None:
    settings = _production_settings(posthog_enabled=True)
    assert settings.posthog_observability_enabled is True
    assert settings.posthog_flags_token == settings.posthog_project_token


def test_production_app_with_posthog_off_sends_nothing(posthog_spies) -> None:
    settings = _production_settings()
    assert settings.posthog_observability_enabled is False
    assert settings.posthog_flags_token is None

    app = create_app(build_application_container(settings))

    @app.get("/boom-logged")
    async def boom_logged():
        try:
            raise RuntimeError("demo failure")
        except RuntimeError:
            logging.getLogger("app.test").exception(
                "test.failure", extra={"event": "test.failure"}
            )
        return {"ok": True}

    client = TestClient(app, base_url="https://demo.example.dev")
    assert client.get("/health").status_code == 200
    assert client.get("/boom-logged").status_code == 200

    _assert_no_posthog_call(posthog_spies)


async def test_flags_are_off_without_calling_posthog(posthog_spies) -> None:
    settings = _production_settings(database_transaction_pooler_enabled=True)

    assert await build_flag(settings, SPOTIFY_NOW_PLAYING)() is False
    assert await build_percentage_provider(settings)() == 0

    _assert_no_posthog_call(posthog_spies)


async def test_agent_chat_analytics_is_skipped(posthog_spies) -> None:
    service = SimpleNamespace(settings=_production_settings(), _posthog=None)

    await AgentChatService._track(
        service, {"input_tokens": 1}, 0.1, False, "trace", 3, "admin:1"
    )

    assert service._posthog is None
    _assert_no_posthog_call(posthog_spies)
