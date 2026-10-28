"""Demo login (app/auth/demo_login.py): off by default, a long code, fake people only."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import BigInteger, Integer, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.demo_guard import DemoDatabaseGuard
from app.auth.demo_login import DemoLoginRefused, demo_code_matches, demo_login_enabled
from app.config import Settings, validate_production_secrets
from app.db.metadata import public_metadata
from app.db.rate_limit import InMemoryRateLimiter
from app.db.session import session_scope
from app.domain.admin_accounts.tables import user_accounts
from app.domain.mobile_card.errors import MobileCardInvalidAccessCodeError
from app.domain.mobile_card.repository import MobileCardSnapshot
from app.domain.mobile_card.service import MobileCardService
from app.domain.volunteers.tables import volunteer_records
from app.runtime import app_lifespan, check_demo_database

CODE = "demo-kode-1234"
DEMO_EMAIL = "demo.stjerne@example.dev"


def _settings(**values) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="test-secret",
        **{"demo_login_enabled": True, "demo_access_code": CODE, **values},
    )


# ------------------------------------------------------------------ settings


def test_demo_login_is_off_by_default() -> None:
    settings = Settings(_env_file=None, app_env="test")
    assert settings.demo_login_enabled is False
    assert not demo_login_enabled(settings)
    assert not demo_code_matches(settings, "")
    # A code alone does not switch it on.
    with_code = Settings(_env_file=None, app_env="test", demo_access_code=CODE)
    assert not demo_login_enabled(with_code)
    assert not demo_code_matches(with_code, CODE)


@pytest.mark.parametrize("code", [None, "", "   ", "elleve-tegn"])
def test_enabled_with_a_short_or_missing_code_refuses_to_start(code) -> None:
    settings = Settings(_env_file=None, app_env="test", demo_login_enabled="true", demo_access_code=code)
    with pytest.raises(ValueError, match="DEMO_ACCESS_CODE of at least 12 characters"):
        validate_production_secrets(settings)


def test_a_twelve_character_code_is_accepted() -> None:
    settings = validate_production_secrets(_settings(demo_access_code="tolv-tegn-ok"))
    assert demo_login_enabled(settings)
    assert demo_code_matches(settings, " tolv-tegn-ok ")
    assert not demo_code_matches(settings, "tolv-tegn-OK")
    assert CODE not in repr(_settings())


# ------------------------------------------------------- the database check


@pytest.fixture
async def runtime(monkeypatch):
    tables = [volunteer_records, user_accounts]
    for table in tables:
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                monkeypatch.setattr(column, "type", column.type.with_variant(Integer, "sqlite"))
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", execution_options={"schema_translate_map": {"public": None}}
    )
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: public_metadata.create_all(conn, tables=tables))
    yield SimpleNamespace(session_factory=async_sessionmaker(engine))
    await engine.dispose()


async def _add_volunteer(runtime, email: str | None) -> None:
    async with session_scope(runtime) as db:
        await db.execute(
            insert(volunteer_records).values(
                first_name="Test", last_name="Person", email=email, gender="A",
                birth_date=date(1999, 1, 1), created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )


async def _add_user(runtime, email: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with session_scope(runtime) as db:
        await db.execute(
            insert(user_accounts).values(
                auth_user_id=uuid4(), username=email.split("@")[0], email=email, role="Admin",
                is_legacy_account=False, onboarding_status="active", created_at=now, updated_at=now,
            )
        )


def _container(runtime, settings: Settings, guard: DemoDatabaseGuard | None = None):
    async def aclose() -> None:
        return None

    return SimpleNamespace(
        settings=settings,
        database_runtime_manager=SimpleNamespace(get_runtime=lambda: runtime),
        demo_guard=guard or DemoDatabaseGuard(),
        aclose=aclose,
    )


async def test_startup_passes_when_everyone_is_fake(runtime) -> None:
    await _add_volunteer(runtime, DEMO_EMAIL)
    await _add_volunteer(runtime, "Fyll.Person.Demo3@Example.dev")
    await _add_user(runtime, "demo.leder@example.dev")
    container = _container(runtime, _settings())
    await check_demo_database(container)
    assert container.demo_guard._passed


@pytest.mark.parametrize(
    "add",
    [
        lambda runtime: _add_volunteer(runtime, "person@kvarteret.no"),
        lambda runtime: _add_volunteer(runtime, None),
        lambda runtime: _add_volunteer(runtime, "someone@example.dev.real.no"),
        lambda runtime: _add_user(runtime, "dev@kvarteret.dev"),
    ],
    ids=["real volunteer", "volunteer without email", "lookalike domain", "real user account"],
)
async def test_startup_refuses_a_database_with_a_real_email(runtime, add) -> None:
    await _add_volunteer(runtime, DEMO_EMAIL)
    await add(runtime)
    container = _container(runtime, _settings())
    with pytest.raises(DemoLoginRefused, match="1 volunteer"):
        await check_demo_database(container)
    # The lifespan runs the same check, so the app does not start.
    app = SimpleNamespace(state=SimpleNamespace(container=container))
    with pytest.raises(DemoLoginRefused):
        async with app_lifespan(app):
            pass


async def test_startup_does_not_look_at_the_database_when_demo_login_is_off(runtime) -> None:
    await _add_volunteer(runtime, "person@kvarteret.no")
    container = _container(runtime, Settings(_env_file=None, app_env="test"))
    await check_demo_database(container)
    assert not container.demo_guard._passed


# ----------------------------------------------------------------- app login


class FakeGuard:
    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.calls = 0

    async def ensure_only_fake_people(self) -> None:
        self.calls += 1
        if self.refuse:
            raise DemoLoginRefused("real people")


class FakeRepository:
    def __init__(self) -> None:
        self.volunteers = {DEMO_EMAIL: 41, "person@kvarteret.no": 7}
        self.refresh_tokens: list[int | None] = []
        self.stored_codes: list[int] = []

    async def find_volunteers_by_email(self, email: str) -> list[dict]:
        found = self.volunteers.get(email)
        return [] if found is None else [{"id": found, "first_name": "Test", "last_name": "Person"}]

    async def find_volunteer_by_email_and_code(self, **kwargs) -> dict | None:
        return None  # no emailed code was ever stored

    async def store_access_code(self, *, volunteer_id: int, code_hash: str, created_at: datetime) -> None:
        self.stored_codes.append(volunteer_id)

    async def store_refresh_token(self, *, volunteer_id, **kwargs) -> None:
        self.refresh_tokens.append(volunteer_id)

    async def fetch_card_snapshot(self, *, volunteer_id: int, **kwargs) -> MobileCardSnapshot:
        return MobileCardSnapshot(
            volunteer_id=volunteer_id, first_name="Test", last_name="Person", birth_date=None,
            created_at=datetime(2026, 1, 1, tzinfo=UTC), photo_path=None, pingvin_points=0, active_roles=[],
        )


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_email(self, *, recipient_email: str, subject: str, html_body: str) -> None:
        self.sent.append(recipient_email)


def _service(settings: Settings | None = None, guard: FakeGuard | None = None):
    repository, sender = FakeRepository(), FakeEmailSender()
    service = MobileCardService(
        settings or _settings(),
        repository=repository,  # type: ignore[arg-type]
        email_sender=sender,
        rate_limiter=InMemoryRateLimiter(),
        demo_guard=guard if guard is not None else FakeGuard(),
    )
    return service, repository, sender


async def test_a_demo_email_logs_in_to_the_app_with_the_code_as_a_real_volunteer(caplog) -> None:
    service, repository, sender = _service()
    await service.request_access_code(DEMO_EMAIL)
    with caplog.at_level(logging.INFO):
        session = await service.create_session(" Demo.Stjerne@Example.dev ", CODE, source_key="127.0.0.1")

    assert sender.sent == [] and repository.stored_codes == []
    identity = service.authenticate_session(session.session_token)
    assert identity.volunteer_id == 41 and not identity.is_review
    assert session.card.person_id == 41
    assert session.refresh_token and repository.refresh_tokens == [41]
    records = [r for r in caplog.records if r.event == "auth.demo_login.succeeded"]
    assert [r.event_data["demo_account"] for r in records] == ["demo.stjerne"]
    assert records[0].event_data["login_kind"] == "app"
    assert CODE not in caplog.text


async def test_the_code_does_not_work_for_any_other_email() -> None:
    service, _, sender = _service()
    with pytest.raises(MobileCardInvalidAccessCodeError):
        await service.create_session("person@kvarteret.no", CODE)
    # Requesting a code for a real email is unchanged: one is stored and sent.
    await service.request_access_code("person@kvarteret.no")
    assert sender.sent == ["person@kvarteret.no"]


@pytest.mark.parametrize("code", ["wrong-code-1234", CODE.upper(), "123456", ""])
async def test_a_wrong_code_is_refused(code) -> None:
    service, repository, _ = _service()
    with pytest.raises(MobileCardInvalidAccessCodeError):
        await service.create_session(DEMO_EMAIL, code)
    assert repository.refresh_tokens == []


async def test_a_demo_email_with_no_volunteer_is_refused() -> None:
    service, _, _ = _service()
    with pytest.raises(MobileCardInvalidAccessCodeError):
        await service.create_session("nobody@example.dev", CODE)


async def test_the_app_login_is_refused_when_the_database_check_fails() -> None:
    guard = FakeGuard(refuse=True)
    service, repository, _ = _service(guard=guard)
    with pytest.raises(MobileCardInvalidAccessCodeError):
        await service.create_session(DEMO_EMAIL, CODE)
    assert guard.calls == 1 and repository.refresh_tokens == []


async def test_the_code_does_nothing_when_demo_login_is_off() -> None:
    service, _, sender = _service(settings=Settings(_env_file=None, app_env="test", app_secret_key="test-secret"))
    with pytest.raises(MobileCardInvalidAccessCodeError):
        await service.create_session(DEMO_EMAIL, CODE)
    await service.request_access_code(DEMO_EMAIL)
    assert sender.sent == [DEMO_EMAIL]  # behaves exactly as today


async def test_without_a_wired_guard_demo_login_stays_off() -> None:
    service = MobileCardService(
        _settings(),
        repository=FakeRepository(),  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )
    with pytest.raises(MobileCardInvalidAccessCodeError):
        await service.create_session(DEMO_EMAIL, CODE)
