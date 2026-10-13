import os
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Public production identifiers. They are not secrets, so they are committed
# here instead of being delivered from Infisical; environment variables still
# override them. They apply only when APP_ENV=production so local runs never
# share production cookies or analytics.
PRODUCTION_DEFAULTS = {
    "app_public_base_url": "https://personal.samfunnetibergen.no",
    "session_cookie_domain": ".samfunnetibergen.no",
    "posthog_project_token": "phc_BKDMEjKXXhntNVQW4h2JDHJy95CLgs28PekPzbNoGS69",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # The infra profile resolver sets this guard for every managed
        # process.  That keeps a stale checkout-local .env from filling in
        # values that were intentionally absent from the selected profile.
        env_file=None if os.getenv("KVARERET_DISABLE_DOTENV") == "1" else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] | None = Field(
        default=None
    )
    # Development-harness credentials; refused in production.
    dev_admin_email: str | None = Field(default=None)
    dev_admin_password: str | None = Field(default=None)
    app_secret_key: str = Field(default="change-me")
    legacy_admin_login_enabled: bool = Field(default=True)
    app_public_base_url: str | None = Field(default=None)
    website_base_url: str = Field(default="https://www.samfunnetibergen.no")
    # One key signs every website -> Personal request; the previous key is
    # accepted during zero-downtime rotation.
    personal_signing_secret: str | None = Field(default=None)
    personal_signing_previous_secret: str | None = Field(default=None)
    # Deprecated per-route names, still accepted until the website has
    # switched to PERSONAL_SIGNING_SECRET.
    event_interest_secret: str | None = Field(default=None)
    volunteer_prospect_hmac_secret: str | None = Field(default=None)
    volunteer_prospect_hmac_previous_secret: str | None = Field(default=None)
    volunteer_prospect_max_body_bytes: int = Field(default=16 * 1024, gt=0)
    volunteer_prospect_route_limit: int = Field(default=120, gt=0)
    volunteer_prospect_route_window_seconds: int = Field(default=60, gt=0)
    volunteer_prospect_client_limit: int = Field(default=10, gt=0)
    volunteer_prospect_client_window_seconds: int = Field(default=600, gt=0)
    supabase_url: str | None = Field(default=None)
    supabase_secret_key: str | None = Field(default=None)
    azure_blob_connection_string: str | None = Field(default=None)
    azure_blob_account_name: str | None = Field(default=None)
    azure_blob_account_key: str | None = Field(default=None)
    azure_photo_container: str = Field(default="images")
    photo_upload_max_bytes: int = Field(default=40 * 1024 * 1024)
    photo_max_dimension: int = Field(default=2048)
    photo_default_size: int = Field(default=512)
    database_url: str | None = Field(default=None)
    # Canary connections use the same Supabase host/credentials on port 6543.
    # Emergency off switch; rollout percentage lives in PostHog.
    database_transaction_pooler_enabled: bool = Field(default=False)
    database_transaction_pooler_connect_timeout_seconds: float = Field(default=2, gt=0, le=10)
    database_transaction_pooler_cooldown_seconds: float = Field(default=60, gt=0)
    database_use_null_pool: bool = Field(default=False)
    database_pool_size: int = Field(default=5)
    database_max_overflow: int = Field(default=5)
    database_pool_timeout_seconds: int = Field(default=10)
    database_pool_recycle_seconds: int = Field(default=1800)
    # Linear feedback integration — all products route through this backend.
    # The ids are public workspace identifiers; only the API key is secret.
    linear_api_key: str | None = Field(default=None)
    linear_team_id: str | None = Field(default="6ea0cddf-c6ed-4c10-a9ac-7884249ea35a")
    linear_project_id_personal: str | None = Field(
        default="73181b8f-728c-4251-b649-ade7d88d44c5"
    )
    linear_project_id_internbevis: str | None = Field(
        default="76554661-df42-482b-9d84-a794aaebe9e5"
    )
    linear_project_id_nettside: str | None = Field(
        default="6b3c6f1b-c7fa-413e-a982-6a934893045a"
    )
    linear_state_id_triage: str | None = Field(
        default="396268d4-7abf-4783-801b-a04d3d7db580"
    )
    # slack_feedback_webhook_url: str | None = Field(default=None)
    resend_api_key: str | None = Field(default=None, repr=False)
    email_sender_name: str = Field(default="Samfunnet i Bergen")
    email_sender_email: str = Field(default="hallaien@samfunnetibergen.no")
    # Public OAuth client id; the client secret and refresh token gate use.
    spotify_client_id: str | None = Field(default="e9ab845ebdfe4b7db5202dc60d9d0f04")
    spotify_client_secret: str | None = Field(default=None)
    spotify_refresh_token: str | None = Field(default=None)
    now_playing_cache_seconds: float = Field(default=10.0)
    now_playing_stale_grace_seconds: float = Field(default=30.0)
    review_bypass_enabled: bool = Field(default=False)
    review_bypass_email: str | None = Field(default=None)
    review_bypass_token: str | None = Field(default=None)
    log_level: str = Field(default="INFO")
    posthog_observability_enabled: bool = Field(default=False)
    posthog_project_token: str | None = Field(default=None)
    posthog_feature_flags_token: str | None = Field(default=None, repr=False)
    posthog_host: str = Field(default="https://eu.i.posthog.com")
    otel_service_name: str = Field(default="kvarteret-personal")
    email_dispatch_enabled: bool = Field(default=True)
    cron_secret: str | None = Field(default=None)
    session_cookie_name: str = Field(default="kvarteret_session")
    session_cookie_domain: str | None = Field(default=None)
    session_ttl_hours: int = Field(default=12)
    session_cache_ttl_seconds: int = Field(default=300)
    login_attempt_limit: int = Field(default=10)
    login_attempt_window_seconds: int = Field(default=900)
    password_reset_attempt_limit: int = Field(default=5)
    password_reset_attempt_window_seconds: int = Field(default=3600)
    pending_volunteer_applications_cache_ttl_seconds: int = Field(default=30)
    volunteer_detail_cache_ttl_seconds: int = Field(default=300)
    admin_accounts_cache_ttl_seconds: int = Field(default=60)
    mobile_card_access_code_ttl_minutes: int = Field(default=10)
    mobile_card_access_code_request_limit: int = Field(default=5)
    mobile_card_access_code_request_window_seconds: int = Field(default=300)
    mobile_card_session_attempt_limit: int = Field(default=5)
    mobile_card_session_attempt_window_seconds: int = Field(default=600)
    mobile_card_session_ttl_days: int = Field(default=90)
    mobile_card_session_renewal_threshold_days: int = Field(default=30)
    feedback_submission_limit: int = Field(default=5)
    feedback_submission_window_seconds: int = Field(default=600)

    @field_validator(
        "app_env",
        "dev_admin_email",
        "dev_admin_password",
        "app_secret_key",
        "app_public_base_url",
        "personal_signing_secret",
        "personal_signing_previous_secret",
        "volunteer_prospect_hmac_secret",
        "volunteer_prospect_hmac_previous_secret",
        "supabase_url",
        "supabase_secret_key",
        "azure_blob_connection_string",
        "azure_blob_account_name",
        "azure_blob_account_key",
        "azure_photo_container",
        "database_url",
        "linear_api_key",
        "linear_team_id",
        "linear_project_id_personal",
        "linear_project_id_internbevis",
        "linear_project_id_nettside",
        "linear_state_id_triage",
        "resend_api_key",
        "email_sender_name",
        "email_sender_email",
        "spotify_client_id",
        "spotify_client_secret",
        "spotify_refresh_token",
        "review_bypass_email",
        "review_bypass_token",
        "log_level",
        "posthog_project_token",
        "posthog_feature_flags_token",
        "posthog_host",
        "otel_service_name",
        "cron_secret",
        "session_cookie_name",
        "session_cookie_domain",
        mode="before",
    )
    @classmethod
    def strip_string_settings(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None if value is not None else value
        return value

    @field_validator("review_bypass_enabled", mode="before")
    @classmethod
    def strip_boolean_settings(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def apply_production_defaults(self):
        if self.app_env == "production":
            for name, value in PRODUCTION_DEFAULTS.items():
                if getattr(self, name) is None:
                    setattr(self, name, value)
        return self

    @property
    def volunteer_signing_secrets(self) -> tuple[str, ...]:
        """Keys accepted for volunteer prospect and booking request signatures."""
        return _signing_secrets(
            self.personal_signing_secret,
            self.personal_signing_previous_secret,
            self.volunteer_prospect_hmac_secret,
            self.volunteer_prospect_hmac_previous_secret,
        )

    @property
    def event_interest_signing_secrets(self) -> tuple[str, ...]:
        """Keys accepted for event interest signatures."""
        return _signing_secrets(
            self.personal_signing_secret,
            self.personal_signing_previous_secret,
            self.event_interest_secret,
        )


def _signing_secrets(*secrets: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(secret for secret in secrets if secret))


def validate_production_secrets(settings: Settings) -> Settings:
    if settings.app_env is None:
        msg = "APP_ENV must be explicitly set to development, test, or production."
        raise ValueError(msg)
    if settings.app_env != "development" and (
        settings.dev_admin_email or settings.dev_admin_password
    ):
        msg = "DEV_ADMIN_EMAIL/DEV_ADMIN_PASSWORD are development-only settings."
        raise ValueError(msg)
    if settings.app_env == "production":
        if settings.app_secret_key == "change-me" or len(settings.app_secret_key) < 32:
            msg = (
                "APP_SECRET_KEY must be a non-default value of at least 32 "
                "characters in production."
            )
            raise ValueError(msg)
        if not settings.app_public_base_url:
            msg = "APP_PUBLIC_BASE_URL is required in production."
            raise ValueError(msg)
        public_url = urlsplit(settings.app_public_base_url)
        if (
            public_url.scheme != "https"
            or not public_url.netloc
            or public_url.username is not None
            or public_url.password is not None
            or public_url.path not in {"", "/"}
            or public_url.query
            or public_url.fragment
        ):
            msg = (
                "APP_PUBLIC_BASE_URL must be an HTTPS origin without credentials, "
                "a path, query, or fragment in production."
            )
            raise ValueError(msg)
        if not any(
            secret and len(secret) >= 32
            for secret in (
                settings.personal_signing_secret,
                settings.volunteer_prospect_hmac_secret,
            )
        ):
            msg = (
                "PERSONAL_SIGNING_SECRET must be at least 32 characters "
                "in production."
            )
            raise ValueError(msg)
        for name, secret in (
            ("PERSONAL_SIGNING_SECRET", settings.personal_signing_secret),
            (
                "PERSONAL_SIGNING_PREVIOUS_SECRET",
                settings.personal_signing_previous_secret,
            ),
            ("VOLUNTEER_PROSPECT_HMAC_SECRET", settings.volunteer_prospect_hmac_secret),
            (
                "VOLUNTEER_PROSPECT_HMAC_PREVIOUS_SECRET",
                settings.volunteer_prospect_hmac_previous_secret,
            ),
        ):
            if secret and len(secret) < 32:
                msg = f"{name} must be at least 32 characters when configured."
                raise ValueError(msg)
    if settings.posthog_observability_enabled and not settings.posthog_project_token:
        msg = "POSTHOG_PROJECT_TOKEN is required when PostHog observability is enabled."
        raise ValueError(msg)
    if settings.app_env == "production" and settings.email_dispatch_enabled:
        if not settings.cron_secret:
            msg = (
                "CRON_SECRET is required when email dispatch is enabled in production."
            )
            raise ValueError(msg)
    return settings


def get_settings() -> Settings:
    return validate_production_secrets(Settings())
