from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.metadata import public_metadata
from app.db.session import reset_request_session, set_request_session
from app.domain.volunteer_applications.models import VolunteerApplicationConflictError
from app.domain.volunteer_applications.repository import VolunteerApplicationsRepository
from app.domain.volunteer_applications.service import VolunteerApplicationsService
from app.domain.volunteer_applications.tables import volunteer_application_invites
from app.domain.volunteers.repository import VolunteersRepository
from app.domain.volunteers.tables import volunteer_records


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"public": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: public_metadata.create_all(
                conn, tables=[volunteer_records, volunteer_application_invites]
            )
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        token = set_request_session(db)
        yield db
        reset_request_session(token)
    await engine.dispose()


async def _seed(db):
    now = datetime.now(UTC)
    await db.execute(
        insert(volunteer_records).values(
            id=1, last_name="Olavsrud", email="typo@olavseud.com",
            phone="+4794253601", created_at=now, gender="A",
        )
    )
    await db.execute(
        insert(volunteer_application_invites).values(
            id=296, token="t", email="typo@olavseud.com", created_at=now,
            promoted_volunteer_id=1, status="volunteer", source="public_signup", trial_shift_attended=False,
        )
    )


@pytest.mark.asyncio
async def test_profile_email_edit_updates_promoted_application(session) -> None:
    await _seed(session)
    await VolunteersRepository().update_volunteer_profile(
        volunteer_id=1, first_name=None, last_name="Olavsrud",
        email="Ida-Maria@Olavsrud.com", phone="+4794253601", birth_date=None,
        gender_code="A", address=None, postal_code=None,
    )
    email = await session.scalar(
        select(volunteer_application_invites.c.email).where(
            volunteer_application_invites.c.id == 296
        )
    )
    assert email == "ida-maria@olavsrud.com"


@pytest.mark.asyncio
async def test_phone_lookup_finds_existing_volunteer(session) -> None:
    await _seed(session)
    repo = VolunteerApplicationsRepository()
    assert await repo.find_volunteer_ids_by_phone("94253601") == [1]
    assert await repo.find_volunteer_ids_by_phone(None) == []


@pytest.mark.asyncio
async def test_phone_match_blocks_second_profile() -> None:
    class Repo:
        async def find_volunteer_ids_by_phone(self, phone):
            return [10645]

    service = VolunteerApplicationsService(
        volunteer_creator=None,
        settings=Settings(app_secret_key="test-secret"),
        repository=Repo(),
        email_outbox=None,
    )
    with pytest.raises(VolunteerApplicationConflictError, match="#10645"):
        await service._reject_phone_duplicate(SimpleNamespace(phone="+4794253601"))
