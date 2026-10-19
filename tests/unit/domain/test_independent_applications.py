"""Exercise independent applications against real SQL persistence without production I/O."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import BigInteger, DefaultClause, Integer, func, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db.metadata import public_metadata
from app.db.session import reset_request_session, set_request_session
from app.domain.admin_accounts.tables import user_accounts
from app.domain.groups.tables import groups
from app.domain.mobile_card.repository import MobileCardRepository
from app.domain.mobile_card.tables import (
    mobile_card_trial_access_codes,
    mobile_card_access_codes,
    mobile_card_refresh_tokens,
)
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.volunteer_applications.models import (
    PublicProspectRegistrationInput,
    VolunteerApplicationFieldConflictError,
    VolunteerApplicationSubmissionInput,
)
from app.domain.volunteer_applications.repository import VolunteerApplicationsRepository
from app.domain.volunteer_applications.service import VolunteerApplicationsService
from app.domain.volunteer_applications.tables import (
    domain_events,
    volunteer_application_friend_invitations,
    volunteer_application_invites,
    volunteer_application_submissions,
    volunteer_prospect_idempotency_keys,
    volunteer_prospect_submissions,
)
from app.domain.volunteers.repository import VolunteersRepository
from app.domain.volunteers.service import VolunteersService
from app.domain.volunteers.tables import volunteer_records, volunteer_photos
from app.shared.semester import get_current_semester_code


class Outbox:
    def __init__(self):
        self.requests = []

    async def enqueue(self, request):
        self.requests.append(request)
        return uuid4()

    async def dispatch_due(self, **kwargs):
        pass


@pytest.fixture
async def stack(monkeypatch):
    tables = [
        groups,
        assignment_roles,
        role_assignments,
        volunteer_records,
        volunteer_photos,
        user_accounts,
        domain_events,
        volunteer_application_invites,
        volunteer_application_submissions,
        volunteer_application_friend_invitations,
        volunteer_prospect_idempotency_keys,
        volunteer_prospect_submissions,
        mobile_card_trial_access_codes,
        mobile_card_access_codes,
        mobile_card_refresh_tokens,
    ]
    # SQLite's generated IDs and timestamps emulate production defaults in these tests.
    for table in tables:
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                monkeypatch.setattr(
                    column, "type", column.type.with_variant(Integer, "sqlite")
                )
            if column.name == "created_at":
                monkeypatch.setattr(
                    column, "server_default", DefaultClause(func.current_timestamp())
                )
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"public": None}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: public_metadata.create_all(conn, tables=tables)
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        token = set_request_session(session)
        try:
            await session.execute(
                insert(groups),
                [
                    dict(
                        id=1,
                        slug="debatt",
                        name="Debatt",
                        is_active=True,
                        discount_tier=1,
                        active_through_semester=20992,
                    ),
                    dict(
                        id=2,
                        slug="fest",
                        name="Fest",
                        is_active=True,
                        discount_tier=2,
                        active_through_semester=20992,
                    ),
                    dict(
                        id=3,
                        slug="skjenke-gruppen",
                        name="Skjenkegruppen",
                        is_active=True,
                        discount_tier=2,
                        active_through_semester=20992,
                    ),
                ],
            )
            await session.execute(
                insert(assignment_roles),
                [
                    dict(id=11, group_id=1, name="Medlem", penguin_points=1),
                    dict(id=22, group_id=2, name="Medlem", penguin_points=1),
                    dict(
                        id=31, group_id=3, name="Halvtimen-skjenker", penguin_points=1
                    ),
                    dict(id=32, group_id=3, name="Pubdyr", penguin_points=1),
                ],
            )
            await session.commit()
            outbox = Outbox()
            repo = VolunteerApplicationsRepository()
            service = VolunteerApplicationsService(
                settings=Settings(app_secret_key="test"),
                repository=repo,
                volunteer_creator=VolunteersService(repository=VolunteersRepository()),
                email_outbox=outbox,
            )
            yield session, service, repo, outbox
        finally:
            reset_request_session(token)
    await engine.dispose()


def signup(first="debatt", second=None, friends=None):
    return PublicProspectRegistrationInput(
        full_name="Test Applicant",
        email="applicant@example.com",
        phone="+4741234567",
        study_institution="UiB",
        background_details=None,
        first_choice_group_slug=first,
        second_choice_group_slug=second,
        friend_emails=friends,
    )


async def complete_profile(session, application_id):
    await session.execute(
        update(volunteer_application_invites)
        .where(volunteer_application_invites.c.id == application_id)
        .values(full_profile_submitted_at=datetime.now(UTC))
    )
    await session.execute(
        update(volunteer_application_submissions)
        .where(volunteer_application_submissions.c.invite_id == application_id)
        .values(
            birth_date=datetime(2000, 1, 1),
            street_address="Street 1",
            postal_code="5000",
            photo_sha1="snapshot",
            photo_filetype="jpg",
        )
    )


async def test_batch_has_distinct_tokens_and_replays_all_ids(stack):
    session, service, repo, outbox = stack
    key = uuid4()
    detail = await service.create_public_prospect_registration(
        signup(second="fest"), idempotency_key=key, request_hash="a" * 64
    )
    assert len(detail.registration_ids) == 2
    rows = (
        (await session.execute(select(volunteer_application_invites))).mappings().all()
    )
    assert len({row["token"] for row in rows}) == 2
    assert {row["first_choice_group_id"] for row in rows} == {1, 2}
    assert all(row["second_choice_group_id"] is None for row in rows)
    assert len(outbox.requests) == 2
    replay = await service.create_public_prospect_registration(
        signup(second="fest"), idempotency_key=key, request_hash="a" * 64
    )
    assert replay.registration_ids == detail.registration_ids
    assert len(outbox.requests) == 2
    same_target = await service.create_public_prospect_registration(
        signup(), idempotency_key=uuid4(), request_hash="b" * 64
    )
    assert same_target.registration_id == detail.registration_id
    assert (
        await session.scalar(
            select(func.count()).select_from(volunteer_application_invites)
        )
        == 2
    )
    assert (
        len(outbox.requests) == 3
    )  # A continuation email, without another application.


async def test_accept_a_reject_b_keeps_identity_and_a_membership(stack):
    session, service, repo, outbox = stack
    detail = await service.create_public_prospect_registration(signup(second="fest"))
    a, b = detail.registration_ids
    for application_id in (a, b):
        await service.mark_contacted(application_id)
        await complete_profile(session, application_id)
        await service.start_trial(application_id)
    a_detail = await repo.get_volunteer_application_detail(a)
    b_detail = await repo.get_volunteer_application_detail(b)
    assert a_detail.promoted_volunteer_id == b_detail.promoted_volunteer_id
    assert a_detail.owns_volunteer_profile and not b_detail.owns_volunteer_profile
    assert a_detail.trial_assignment_id != b_detail.trial_assignment_id
    assert (
        await session.scalar(select(func.count()).select_from(volunteer_records)) == 1
    )
    await service.approve_volunteer_application(a, accepted_role_id=11)
    assert (await repo.get_volunteer_application_detail(b)).status == "trial"
    await service.reject_volunteer_application(b)
    assignments = (await session.execute(select(role_assignments))).mappings().all()
    assert len(assignments) == 1
    assert assignments[0]["id"] == a_detail.trial_assignment_id
    assert assignments[0]["contract_signed"] is True
    assert (await repo.get_volunteer_application_detail(a)).status == "volunteer"
    assert await MobileCardRepository().find_volunteers_by_email(
        "applicant@example.com"
    )


async def test_both_approvals_reuse_identity_and_keep_two_assignments(stack):
    session, service, repo, _ = stack
    detail = await service.create_public_prospect_registration(signup(second="fest"))
    identities = []
    for application_id, role_id in zip(detail.registration_ids, (11, 22), strict=True):
        await service.mark_contacted(application_id)
        await complete_profile(session, application_id)
        await service.start_trial(application_id)
        identities.append(
            await service.approve_volunteer_application(
                application_id, accepted_role_id=role_id
            )
        )
    assert identities[0] == identities[1]
    assert (
        await session.scalar(select(func.count()).select_from(volunteer_records)) == 1
    )
    assert await session.scalar(select(func.count()).select_from(role_assignments)) == 2


async def test_existing_volunteer_adds_group_without_profile_changes(stack):
    session, service, repo, _ = stack
    await session.execute(
        insert(volunteer_records).values(
            id=50,
            email="applicant@example.com",
            first_name="Canonical",
            last_name="Person",
            gender="A",
        )
    )
    await session.execute(
        insert(volunteer_photos).values(
            volunteer_id=50, sha1="canonical", filetype="png"
        )
    )
    await session.execute(
        insert(role_assignments).values(
            id=100,
            volunteer_id=50,
            group_id=1,
            role_id=11,
            semester=get_current_semester_code(),
            contract_signed=True,
        )
    )
    await session.commit()
    with pytest.raises(VolunteerApplicationFieldConflictError):
        await service.create_public_prospect_registration(signup())
    detail = await service.create_public_prospect_registration(signup(first="fest"))
    await service.mark_contacted(detail.registration_id)
    trial = await service.start_trial(detail.registration_id)
    assert trial.promoted_volunteer_id == 50 and not trial.owns_volunteer_profile
    assert await MobileCardRepository().find_volunteers_by_email(
        "applicant@example.com"
    )
    await repo.save_submission(
        registration_id=detail.registration_id,
        email="applicant@example.com",
        submission=VolunteerApplicationSubmissionInput(
            first_name="Snapshot",
            last_name="Changed",
            gender="A",
            birth_date=date(2000, 1, 1),
            address="Street 1",
            postal_code="5000",
            phone="+4741234567",
        ),
        photo_sha1="snapshot",
        photo_filetype="jpg",
    )
    await service.approve_volunteer_application(
        detail.registration_id, accepted_role_id=22
    )
    person = (await session.execute(select(volunteer_records))).mappings().one()
    assert person["first_name"] == "Canonical"
    assert await session.scalar(select(volunteer_photos.c.sha1)) == "canonical"
    assert await session.scalar(select(func.count()).select_from(role_assignments)) == 2


async def test_roles_in_same_parent_group_are_independent(stack):
    session, service, repo, _ = stack
    detail = await service.create_public_prospect_registration(
        signup("halvtimen", "grondahls")
    )
    rows = (
        (await session.execute(select(volunteer_application_invites))).mappings().all()
    )
    assert len(detail.registration_ids) == 2
    assert {row["initial_group_id"] for row in rows} == {3}
    assert {row["initial_role_id"] for row in rows} == {31, 32}


async def test_friend_with_other_application_or_existing_identity_is_allowed(stack):
    session, service, repo, _ = stack
    await session.execute(
        insert(volunteer_records).values(
            id=50,
            email="friend@example.com",
            first_name="Friend",
            last_name="Person",
            gender="A",
        )
    )
    await service.create_public_prospect_registration(
        signup(friends=["friend@example.com"])
    )
    await service.create_public_prospect_registration(
        signup(first="fest", friends=["friend@example.com"])
    )
    rows = (
        (await session.execute(select(volunteer_application_invites))).mappings().all()
    )
    assert len(rows) == 4
    assert len({row["token"] for row in rows}) == 4


async def test_reused_application_does_not_accept_public_profile_overwrite(stack):
    session, service, repo, _ = stack
    first = await service.create_public_prospect_registration(signup())
    changed = signup()
    changed.full_name = "Changed By Public"
    second = await service.create_public_prospect_registration(changed)
    assert second.registration_id == first.registration_id
    assert second.first_name == "Test"


async def test_additional_invalid_target_rolls_back_entire_batch(stack):
    session, service, repo, outbox = stack
    with pytest.raises(Exception, match="finnes ikke"):
        await service.create_public_prospect_registration(signup(second="missing"))
    assert (
        await session.scalar(
            select(func.count()).select_from(volunteer_application_invites)
        )
        == 0
    )
    assert outbox.requests == []


async def test_deleting_secondary_application_invalidates_batch_claim(stack):
    session, service, repo, _ = stack
    detail = await service.create_public_prospect_registration(
        signup(second="fest"), idempotency_key=uuid4(), request_hash="a" * 64
    )
    await repo.delete_volunteer_application(detail.registration_ids[1])
    assert (
        await session.scalar(
            select(func.count()).select_from(volunteer_prospect_submissions)
        )
        == 0
    )
    assert (
        await repo.get_volunteer_application_detail(detail.registration_id) is not None
    )


async def test_access_code_remains_attached_to_issuing_trial(stack):
    from app.db.rate_limit import InMemoryRateLimiter
    from app.domain.mobile_card.service import MobileCardService
    from app.domain.mobile_card.errors import MobileCardInvalidAccessCodeError

    session, service, repo, _ = stack
    detail = await service.create_public_prospect_registration(signup(second="fest"))
    a, b = detail.registration_ids
    await service.mark_contacted(a)
    await service.start_trial(a)
    mobile = MobileCardService(
        settings=Settings(app_secret_key="test"),
        repository=MobileCardRepository(),
        email_sender=None,
        rate_limiter=InMemoryRateLimiter(),
        trial_applicant_provider=service,
    )
    await mobile.repository.store_trial_access_code(
        application_id=a,
        code_hash=mobile._hash_access_code("123456"),
        created_at=datetime.now(UTC),
    )
    await service.mark_contacted(b)
    await service.start_trial(b)
    assert (
        await repo.find_active_trial_applicant_by_email("applicant@example.com")
    ).application_id == b
    card_session = await mobile.create_session("applicant@example.com", "123456")
    assert (
        mobile.sessions.decode_token(card_session.session_token).trial_application_id
        == a
    )
    with pytest.raises(MobileCardInvalidAccessCodeError):
        await mobile.create_session("applicant@example.com", "123456")
