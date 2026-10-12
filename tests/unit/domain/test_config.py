import os
from pathlib import Path
import subprocess
import sys

from app.config import Settings
from app.config import validate_production_secrets
from app.db.session import build_database_runtime
from app.db.session import NullPool


def _settings_app_env_from_checkout(tmp_path: Path, *, disable_dotenv: bool) -> str:
    (tmp_path / ".env").write_text(
        "APP_ENV=production\nAPP_SECRET_KEY=from-checkout-dotenv\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
    environment.pop("APP_ENV", None)
    environment.pop("APP_SECRET_KEY", None)
    if disable_dotenv:
        environment["KVARERET_DISABLE_DOTENV"] = "1"
    else:
        environment.pop("KVARERET_DISABLE_DOTENV", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.config import Settings; print(Settings().app_env or '')",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_managed_session_guard_ignores_checkout_dotenv(tmp_path: Path) -> None:
    assert _settings_app_env_from_checkout(tmp_path, disable_dotenv=True) == ""


def test_unmanaged_session_preserves_dotenv_loading(tmp_path: Path) -> None:
    assert _settings_app_env_from_checkout(tmp_path, disable_dotenv=False) == "production"


def test_validate_production_secrets_requires_explicit_app_env() -> None:
    settings = Settings(app_env=None)

    try:
        validate_production_secrets(settings)
    except ValueError as exc:
        assert str(exc) == (
            "APP_ENV must be explicitly set to development, test, or production."
        )
    else:
        raise AssertionError("Expected missing APP_ENV validation to fail.")


def test_settings_strip_boolean_whitespace() -> None:
    settings = Settings.model_validate({"review_bypass_enabled": "false\n"})

    assert settings.review_bypass_enabled is False


def test_settings_strip_string_whitespace() -> None:
    settings = Settings.model_validate(
        {
            "app_public_base_url": " https://example.com/ \n",
            "azure_photo_container": " images \n",
        }
    )

    assert settings.app_public_base_url == "https://example.com/"
    assert settings.azure_photo_container == "images"


def test_validate_production_secrets_rejects_default_secret() -> None:
    settings = Settings(app_env="production", app_secret_key="change-me")

    try:
        validate_production_secrets(settings)
    except ValueError as exc:
        assert str(exc) == (
            "APP_SECRET_KEY must be a non-default value of at least 32 characters "
            "in production."
        )
    else:
        raise AssertionError("Expected production secret validation to fail.")


def test_validate_production_secrets_rejects_missing_public_base_url() -> None:
    settings = Settings(
        app_env="production",
        app_secret_key="a-production-secret-with-32-characters",
        app_public_base_url=None,
        email_dispatch_enabled=False,
    )

    try:
        validate_production_secrets(settings)
    except ValueError as exc:
        assert str(exc) == "APP_PUBLIC_BASE_URL is required in production."
    else:
        raise AssertionError("Expected production public URL validation to fail.")


def test_validate_production_secrets_rejects_insecure_public_base_url() -> None:
    settings = Settings(
        app_env="production",
        app_secret_key="a-production-secret-with-32-characters",
        app_public_base_url="http://personal.example.test/app",
        email_dispatch_enabled=False,
    )

    try:
        validate_production_secrets(settings)
    except ValueError as exc:
        assert str(exc) == (
            "APP_PUBLIC_BASE_URL must be an HTTPS origin without credentials, a path, "
            "query, or fragment in production."
        )
    else:
        raise AssertionError("Expected production public URL validation to fail.")


def test_validate_production_secrets_requires_signing_secret() -> None:
    settings = Settings(
        app_env="production",
        app_secret_key="a-production-secret-with-32-characters",
        app_public_base_url="https://personal.example.test",
        volunteer_prospect_hmac_secret=None,
        email_dispatch_enabled=False,
    )

    try:
        validate_production_secrets(settings)
    except ValueError as exc:
        assert str(exc) == (
            "PERSONAL_SIGNING_SECRET must be at least 32 characters "
            "in production."
        )
    else:
        raise AssertionError("Expected signing secret validation to fail.")


def test_validate_production_secrets_rejects_short_previous_hmac_secret() -> None:
    settings = Settings(
        app_env="production",
        app_secret_key="a-production-secret-with-32-characters",
        app_public_base_url="https://personal.example.test",
        volunteer_prospect_hmac_secret="a-prospect-hmac-secret-with-32-characters",
        volunteer_prospect_hmac_previous_secret="too-short",
        email_dispatch_enabled=False,
    )

    try:
        validate_production_secrets(settings)
    except ValueError as exc:
        assert str(exc) == (
            "VOLUNTEER_PROSPECT_HMAC_PREVIOUS_SECRET must be at least 32 "
            "characters when configured."
        )
    else:
        raise AssertionError("Expected previous HMAC secret validation to fail.")


def test_validate_production_secrets_accepts_complete_production_settings() -> None:
    settings = Settings(
        app_env="production",
        app_secret_key="a-production-secret-with-32-characters",
        app_public_base_url="https://personal.example.test",
        volunteer_prospect_hmac_secret="a-prospect-hmac-secret-with-32-characters",
        email_dispatch_enabled=False,
    )

    assert validate_production_secrets(settings) is settings


def test_validate_production_secrets_accepts_shared_signing_secret_alone() -> None:
    settings = Settings(
        app_env="production",
        app_secret_key="a-production-secret-with-32-characters",
        app_public_base_url="https://personal.example.test",
        personal_signing_secret="a-personal-signing-secret-with-32-chars",
        volunteer_prospect_hmac_secret=None,
        email_dispatch_enabled=False,
    )

    assert validate_production_secrets(settings) is settings


def test_validate_production_secrets_rejects_short_shared_signing_secret() -> None:
    settings = Settings(
        app_env="production",
        app_secret_key="a-production-secret-with-32-characters",
        app_public_base_url="https://personal.example.test",
        personal_signing_secret="too-short",
        volunteer_prospect_hmac_secret="a-prospect-hmac-secret-with-32-characters",
        email_dispatch_enabled=False,
    )

    try:
        validate_production_secrets(settings)
    except ValueError as exc:
        assert str(exc) == (
            "PERSONAL_SIGNING_SECRET must be at least 32 characters when configured."
        )
    else:
        raise AssertionError("Expected shared signing secret validation to fail.")


def test_signing_secrets_prefer_shared_key_and_keep_route_fallbacks() -> None:
    settings = Settings(
        app_env="test",
        personal_signing_secret="shared",
        personal_signing_previous_secret="previous",
        volunteer_prospect_hmac_secret="shared",
        volunteer_prospect_hmac_previous_secret="volunteer-previous",
        event_interest_secret="event-interest",
    )

    assert settings.volunteer_signing_secrets == (
        "shared",
        "previous",
        "volunteer-previous",
    )
    assert settings.event_interest_signing_secrets == (
        "shared",
        "previous",
        "event-interest",
    )


def test_build_database_runtime_uses_queue_pool_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_async_engine(database_url: str, **kwargs):
        captured["database_url"] = database_url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("app.db.session.create_async_engine", fake_create_async_engine)
    monkeypatch.setattr("app.db.session.async_sessionmaker", lambda engine, expire_on_commit: ("session-factory", engine, expire_on_commit))

    runtime = build_database_runtime(Settings(database_url="postgresql+asyncpg://example.test/postgres"))

    assert runtime.engine is not None
    assert captured["database_url"] == "postgresql+asyncpg://example.test/postgres"
    assert captured["kwargs"] == {
        "connect_args": {},
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 5,
        "pool_timeout": 10,
        "pool_recycle": 1800,
        "pool_use_lifo": True,
    }


def test_build_database_runtime_uses_null_pool_on_vercel(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_async_engine(database_url: str, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("app.db.session.create_async_engine", fake_create_async_engine)
    monkeypatch.setattr("app.db.session.async_sessionmaker", lambda engine, expire_on_commit: ("session-factory", engine, expire_on_commit))
    monkeypatch.setenv("VERCEL", "1")

    build_database_runtime(Settings(database_url="postgresql+asyncpg://example.test/postgres"))

    assert captured["kwargs"] == {
        "connect_args": {},
        "pool_pre_ping": True,
        "poolclass": NullPool,
    }


def test_build_database_runtime_allows_null_pool_override(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_async_engine(database_url: str, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("app.db.session.create_async_engine", fake_create_async_engine)
    monkeypatch.setattr("app.db.session.async_sessionmaker", lambda engine, expire_on_commit: ("session-factory", engine, expire_on_commit))

    build_database_runtime(
        Settings(
            database_url="postgresql+asyncpg://example.test/postgres",
            database_use_null_pool=True,
        )
    )

    assert captured["kwargs"] == {
        "connect_args": {},
        "pool_pre_ping": True,
        "poolclass": NullPool,
    }
