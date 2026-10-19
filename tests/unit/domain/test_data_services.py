from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.config import Settings
from app.db.rate_limit import InMemoryRateLimiter
from app.db.session import reset_request_session, set_request_session
from app.domain.courses.repository import CoursesRepository
from app.domain.courses.service import (
    CoursesService,
    DuplicateCourseCompletionError,
    InvalidCourseCompletionError,
)
from app.domain.groups.service import GroupsService
from app.domain.volunteer_applications.models import (
    PublicProspectGroup,
    PublicProspectRegistrationResult,
    PublicProspectRegistrationInput,
    TrialApplicantCardSnapshot,
)
from app.domain.volunteer_applications.service import (
    VolunteerApplicationConflictError,
    VolunteerApplicationDetail,
    VolunteerApplicationSubmissionInput,
    VolunteerApplicationValidationError,
    VolunteerApplicationsService,
)
from app.domain.mobile_card.service import (
    MobileCardCurrentCardResult,
    MobileCardInvalidAccessCodeError,
    MobileCardInvalidSessionError,
    MobileCardRateLimitedError,
    MobileCardService,
    _word_of_the_day,
)
from app.domain.mobile_card.repository import (
    MobileCardRoleHistorySnapshot,
    MobileCardRoleSnapshot,
    MobileCardSnapshot,
)
from app.domain.volunteers.service import VolunteersService
from app.domain.volunteers.repository import VolunteersRepository
from app.email_delivery import (
    APPLICANT_APPLICATION_RECEIVED,
    APPLICANT_INVITATION,
    APPLICANT_PROFILE_COMPLETION,
    EmailDeliveryRequest,
)


class FakeVolunteersRepository:
    def __init__(self, list_rows: list[dict]) -> None:
        self.list_rows = list_rows
        self.calls: list[dict[str, int | str | None]] = []

    async def list_volunteers_page(
        self,
        *,
        limit: int,
        after_last_name: str | None = None,
        after_first_name: str | None = None,
        after_volunteer_id: int | None = None,
        only_active: bool = False,
        with_total: bool = False,
    ):
        self.calls.append(
            {
                "method": "list_volunteers_page",
                "limit": limit,
                "after_last_name": after_last_name,
                "after_first_name": after_first_name,
                "after_volunteer_id": after_volunteer_id,
            }
        )
        return self.list_rows


class FakeVolunteerCreator:
    """Stands in for VolunteersService behind VolunteerCreatorProtocol."""

    def __init__(self) -> None:
        self.created: list[dict[str, object | None]] = []

    async def add_application_assignment(self, **kwargs):
        self.created.append(kwargs)
        return kwargs["volunteer_id"], kwargs.get("assignment_id") or 35

    async def remove_trial_assignment(self, **kwargs):
        self.removed = getattr(self, "removed", []) + [kwargs]

    async def create_from_application(self, **kwargs) -> tuple[int, int | None]:
        self.created.append(kwargs)
        return 12, kwargs.get("assignment_id") or 34


class FakeVolunteerApplicationsRepository:
    def __init__(self) -> None:
        self.pending_count = 3
        self.count_calls = 0
        self.saved_registration_ids: list[int] = []
        self.saved_submission_phones: list[str | None] = []
        self.approved_registration_ids: list[int] = []
        self.deleted_registration_ids: list[int] = []
        self.created_invites: list[dict[str, object | None]] = []
        self.existing_volunteer_ids_by_email: dict[str, int] = {}
        self.active_registration_ids_by_email: dict[str, int] = {}
        self.volunteer_ids_by_phone: dict[str, list[int]] = {}
        self.application_photo_sha1: str | None = None
        self.application_photo_filetype: str | None = None
        self.application_photo_url: str | None = None
        self.media_token_service = None
        self.recent_registration_rows: list[dict] = []
        self.public_prospect_groups: dict[str, PublicProspectGroup] = {}
        self.public_prospect_role_ids: dict[tuple[int, str], int] = {}
        self.created_public_prospects: list[dict[str, object | None]] = []
        self.application_submitted = True
        self.detail_status = "new"
        self.promoted_volunteer_id: int | None = None

    async def create_public_prospect_registration(self, **kwargs):
        self.created_public_prospects.append(kwargs)
        from types import SimpleNamespace
        return PublicProspectRegistrationResult(
            detail=SimpleNamespace(
                registration_id=7 + len(self.created_public_prospects) - 1,
                email=kwargs["email"],
            ),
            friend_invites=[],
        )

    async def create_volunteer_application_invitation(
        self,
        *,
        email: str,
        token: str,
        initial_group_id: int | None = None,
        initial_role_id: int | None = None,
    ):
        self.created_invites.append(
            {
                "email": email,
                "token": token,
                "initial_group_id": initial_group_id,
                "initial_role_id": initial_role_id,
            }
        )
        return type(
            "Invite",
            (),
            {
                "registration_id": 7,
                "token": token,
                "email": email,
                "created_at": datetime.fromisoformat("2026-03-13T12:00:00+00:00"),
                "initial_group_id": initial_group_id,
                "initial_group_name": None,
                "initial_role_id": initial_role_id,
                "initial_role_name": None,
            },
        )()

    async def list_volunteer_applications(self):
        raise NotImplementedError

    async def list_recent_volunteer_registrations(self, *, limit: int, before_volunteer_id: int | None = None):
        rows = [
            row
            for row in self.recent_registration_rows
            if before_volunteer_id is None or row["id"] < before_volunteer_id
        ]
        return rows[:limit]

    async def count_pending_volunteer_applications(self) -> int:
        self.count_calls += 1
        return self.pending_count

    async def get_volunteer_application_detail(self, registration_id: int):
        return await self.get_volunteer_application_by_token("token-123")

    async def get_volunteer_application_by_token(self, token: str):
        if token != "token-123":
            return None
        return VolunteerApplicationDetail(
            registration_id=7,
            token="token-123",
            email="registrant@example.com",
            created_at=datetime.fromisoformat("2026-03-13T12:00:00+00:00"),
            submitted=self.application_submitted,
            source="invite",
            status=self.detail_status,
            pending_volunteer_id=8 if self.application_submitted else None,
            first_name="Sample",
            last_name="Registrant",
            phone="00000000",
            birth_date=date(1815, 12, 10),
            gender="K",
            address="Adresse 1",
            postal_code="5000",
            photo_sha1=self.application_photo_sha1,
            photo_filetype=self.application_photo_filetype,
            photo_url=self.application_photo_url,
            study_institution=None,
            background_details=None,
            initial_group_id=3,
            initial_group_name="Bar",
            initial_role_id=getattr(self, "initial_role_id", 9),
            initial_role_name="Skiftleder",
            promoted_volunteer_id=self.promoted_volunteer_id,
            owns_volunteer_profile=getattr(self, "owns_volunteer_profile", False),
            promoted_at=getattr(self, "promoted_at", None),
            trial_assignment_id=getattr(self, "trial_assignment_id", None),
        )

    async def save_submission(
        self,
        *,
        registration_id: int,
        email: str,
        submission: VolunteerApplicationSubmissionInput,
        photo_sha1: str | None,
        photo_filetype: str | None,
    ) -> None:
        self.saved_registration_ids.append(registration_id)
        self.saved_submission_phones.append(submission.phone)

    async def set_application_status(
        self,
        registration_id: int,
        *,
        status,
        start_trial: bool = False,
        volunteer_id: int | None = None,
        trial_assignment_id: int | None = None,
        owns_volunteer_profile: bool | None = None,
        expected_status=None,
    ) -> bool:
        if expected_status is not None and self.detail_status != expected_status:
            return False
        self.detail_status = status.value
        if owns_volunteer_profile is not None:
            self.owns_volunteer_profile = owns_volunteer_profile
        if volunteer_id is not None:
            self.promoted_volunteer_id = volunteer_id
        if start_trial:
            self.trial_assignment_id = trial_assignment_id
        return True

    async def lock_registration_emails(self, emails: list[str]) -> None:
        self.locked_emails = getattr(self, "locked_emails", []) + [emails]

    async def find_volunteer_id_by_email(self, email: str) -> int | None:
        return self.existing_volunteer_ids_by_email.get(email.lower())

    async def find_volunteer_ids_by_phone(self, phone: str | None) -> list[int]:
        return self.volunteer_ids_by_phone.get(phone or "", [])

    async def find_active_registration_id_by_email(self, email: str) -> int | None:
        return self.active_registration_ids_by_email.get(email.lower())

    async def has_target_membership(self, email, **kwargs):
        return False

    async def find_active_registration_for_target(self, email, *, group_id, role_id, exclude_id=None):
        return getattr(self, "active_targets", {}).get((email.lower(), group_id, role_id))

    async def photo_in_use(self, sha1):
        return False

    async def find_public_prospect_groups_by_slugs(self, slugs: list[str]) -> dict[str, PublicProspectGroup]:
        return {slug: self.public_prospect_groups[slug] for slug in slugs if slug in self.public_prospect_groups}

    async def find_public_prospect_role_id(self, *, group_id: int, role_name: str) -> int | None:
        return self.public_prospect_role_ids.get((group_id, role_name))

    async def role_matches_group(self, *, role_id: int, group_id: int) -> bool:
        return True

    async def mark_promoted(
        self,
        *,
        registration_id: int,
        volunteer_id: int,
        accepted_group_id: int,
    ) -> None:
        self.approved_registration_ids.append(registration_id)

    async def delete_volunteer_application(self, registration_id: int) -> None:
        self.deleted_registration_ids.append(registration_id)

    async def append_domain_event(self, event, *, subject_id: int) -> int:
        self.domain_events = getattr(self, "domain_events", [])
        self.domain_events.append((event.event_type, subject_id))
        return len(self.domain_events)


class FakeMobileCardRepository:
    def __init__(
        self,
        volunteers_by_email: list[dict] | None = None,
        volunteer_by_email_and_code: dict | None = None,
        card_snapshot: MobileCardSnapshot | None = None,
    ) -> None:
        self.volunteers_by_email = volunteers_by_email or []
        self.volunteer_by_email_and_code = volunteer_by_email_and_code
        self.card_snapshot = card_snapshot
        self.stored_access_codes: list[tuple[int, str, datetime]] = []
        self.stored_trial_access_codes: list[tuple[int, str, datetime]] = []
        self.accept_trial_code = True
        self.refresh_tokens: dict[str, dict] = {}

    async def store_refresh_token(self, *, token_hash, volunteer_id, application_id, created_at, expires_at) -> None:
        self.refresh_tokens[token_hash] = {
            "volunteer_id": volunteer_id,
            "application_id": application_id,
            "expires_at": expires_at,
        }

    async def use_refresh_token(self, *, token_hash, now, new_expires_at):
        row = self.refresh_tokens.get(token_hash)
        if row is None or row["expires_at"] <= now:
            return None
        row["expires_at"] = new_expires_at
        return row

    async def find_volunteers_by_email(self, email: str) -> list[dict]:
        return list(self.volunteers_by_email)

    async def store_access_code(self, *, volunteer_id: int, code_hash: str, created_at: datetime) -> None:
        self.stored_access_codes.append((volunteer_id, code_hash, created_at))

    async def store_trial_access_code(
        self, *, application_id: int, code_hash: str, created_at: datetime
    ) -> None:
        self.stored_trial_access_codes.append((application_id, code_hash, created_at))

    async def find_trial_application_for_code(self, **kwargs):
        return 77

    async def consume_trial_access_code(self, **kwargs) -> bool:
        return self.accept_trial_code

    async def find_volunteer_by_email_and_code(
        self, *, email: str, code_hash: str, expires_after: datetime
    ) -> dict | None:
        return self.volunteer_by_email_and_code

    async def fetch_card_snapshot(
        self,
        *,
        volunteer_id: int,
        semester_code: int,
        include_role_history: bool = False,
    ):
        if self.card_snapshot is not None and not include_role_history:
            self.card_snapshot.role_history = []
        return self.card_snapshot


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent_emails: list[dict[str, str]] = []

    async def send_email(self, *, recipient_email: str, subject: str, html_body: str) -> None:
        self.sent_emails.append(
            {
                "recipient_email": recipient_email,
                "subject": subject,
                "html_body": html_body,
            }
        )


class FakeTrialApplicantProvider:
    def __init__(self, snapshot: TrialApplicantCardSnapshot | None) -> None:
        self.snapshot = snapshot
        self.approved_volunteer_id = None

    async def find_active_trial_applicant_by_email(
        self, email: str, *, include_expired: bool = False
    ):
        return self.snapshot

    async def get_active_trial_applicant(
        self, application_id: int, *, include_expired: bool = False
    ):
        if self.snapshot and self.snapshot.application_id == application_id:
            return self.snapshot
        return None

    async def get_approved_volunteer_id(self, application_id: int) -> int | None:
        return self.approved_volunteer_id


class FakeEmailOutbox:
    def __init__(self) -> None:
        self.requests: list[EmailDeliveryRequest] = []

    async def enqueue(self, request: EmailDeliveryRequest):
        self.requests.append(request)
        return uuid4()

    async def dispatch_due(self, *, batch_size: int = 10):
        return None


def assert_email_enqueued(
    request: EmailDeliveryRequest,
    *,
    recipient_email: str,
    template_key: str,
) -> None:
    assert request.recipient_email == recipient_email
    assert request.template_key == template_key


class FakeMediaTokenService:
    def build_photo_media_url(self, path: str) -> str:
        return f"/media/photos/{path}?token=test"


class FakeMobileCardAprilStateService:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    async def is_enabled(self) -> bool:
        return self.enabled


def _build_mobile_card_snapshot() -> MobileCardSnapshot:
    return MobileCardSnapshot(
        volunteer_id=12,
        first_name="Ada",
        last_name="Lovelace",
        birth_date=None,
        created_at=datetime(2026, 3, 13, tzinfo=UTC),
        photo_path=None,
        pingvin_points=8,
        active_roles=[
            MobileCardRoleSnapshot(
                name="Shift lead",
                group="Bar",
                group_id=253,
                discount_level=2,
                pingvin_points=4,
                signed_contract=True,
            )
        ],
        role_history=[
            MobileCardRoleHistorySnapshot(
                name="Shift lead",
                group="Bar",
                group_id=253,
                discount_level=2,
                pingvin_points=4,
                signed_contract=True,
                semester=20261,
                is_active=True,
            ),
            MobileCardRoleHistorySnapshot(
                name="Member",
                group="PR",
                group_id=270,
                discount_level=1,
                pingvin_points=2,
                signed_contract=True,
                semester=20252,
                is_active=False,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_groups_service_list_uses_database_query(monkeypatch) -> None:
    service = GroupsService()
    captured = {}

    async def fake_fetch_all_mappings(stmt):
        captured["sql"] = str(stmt)
        return [
            {
                "id": 3,
                "name": "Bar",
                "description": "Drinks",
                "is_active": True,
                "active_through_semester": 20262,
                "parent_group_id": None,
                "discount_tier": 1,
            }
        ]

    monkeypatch.setattr(service, "fetch_all_mappings", fake_fetch_all_mappings)

    rows = await service.list_groups(query="bar", limit=20)

    assert rows[0].group_id == 3
    assert "LIKE lower" in captured["sql"]


@pytest.mark.asyncio
async def test_group_detail_recent_members_are_scoped_to_current_semester(
    monkeypatch,
) -> None:
    service = GroupsService()
    captured = {}

    async def fake_fetch_all_mappings(stmt):
        captured["sql"] = str(stmt)
        return [
            {
                "row_type": "group",
                "group_id": 7,
                "group_name": "Bar",
                "group_description": "Drinks",
                "group_active": True,
                "group_active_until": 20262,
                "group_parent_id": None,
                "group_discount_step": 1,
                "group_created_at": "2026-03-13T12:00:00+00:00",
                "position_id": None,
                "position_name": None,
                "position_points": None,
                "position_assignment_count": None,
                "history_id": None,
                "volunteer_id": None,
                "volunteer_first_name": None,
                "volunteer_last_name": None,
                "volunteer_photo_sha1": None,
                "volunteer_photo_filetype": None,
                "member_role_name": None,
                "member_semester": None,
                "member_contract_signed": None,
            },
            {
                "row_type": "member",
                "group_id": None,
                "group_name": None,
                "group_description": None,
                "group_active": None,
                "group_active_until": None,
                "group_parent_id": None,
                "group_discount_step": None,
                "group_created_at": None,
                "position_id": None,
                "position_name": None,
                "position_points": None,
                "position_assignment_count": None,
                "history_id": 9,
                "volunteer_id": 12,
                "volunteer_first_name": "Ada",
                "volunteer_last_name": "Lovelace",
                "volunteer_photo_sha1": "abc123",
                "volunteer_photo_filetype": "jpg",
                "member_role_name": "Shift lead",
                "member_semester": 20262,
                "member_contract_signed": True,
            },
        ]

    async def fake_get_group_delete_blockers(group_id: int) -> list[str]:
        return []

    monkeypatch.setattr(service, "fetch_all_mappings", fake_fetch_all_mappings)
    monkeypatch.setattr(service, "_get_group_delete_blockers", fake_get_group_delete_blockers)
    monkeypatch.setattr("app.domain.groups.service.get_current_semester_code", lambda: 20262)

    detail = await service.get_group_detail(7)

    assert detail is not None
    assert len(detail.recent_members) == 1
    assert detail.recent_members[0].semester_code == 20262
    assert detail.recent_members[0].photo_url is not None
    assert detail.recent_members[0].photo_url.startswith("/media/photos/abc123.jpg")
    assert "role_assignments.semester = :semester_1" in captured["sql"]


@pytest.mark.asyncio
async def test_groups_service_archive_marks_group_inactive(monkeypatch) -> None:
    service = GroupsService()
    captured = {}

    class FakeResult:
        def first(self):
            return (7,)

    class FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            return FakeResult()

    monkeypatch.setattr("app.domain.groups.service.get_current_semester_code", lambda: 20261)

    token = set_request_session(FakeSession())
    try:
        archived = await service.archive_group(7)
    finally:
        reset_request_session(token)

    assert archived is True
    assert "UPDATE public.groups SET is_active=false" in captured["sql"]
    assert (
        "active_through_semester=CASE WHEN (public.groups.active_through_semester > 20261) THEN 20261"
        in captured["sql"]
    )
    assert "WHERE public.groups.id = 7" in captured["sql"]


@pytest.mark.asyncio
async def test_courses_service_list_uses_database_query(monkeypatch) -> None:
    service = CoursesService(repository=CoursesRepository())
    captured = {}

    async def fake_fetch_all_mappings(stmt):
        captured["sql"] = str(stmt)
        return [
            {
                "id": 9,
                "name": "Ordensvakt",
                "description": "Security",
                "created_at": "2026-03-13T12:00:00+00:00",
            }
        ]

    monkeypatch.setattr(service.repository, "fetch_all_mappings", fake_fetch_all_mappings)

    rows = await service.list_courses(query="ordens", limit=15)

    assert rows[0].course_id == 9
    assert rows[0].created_at == datetime.fromisoformat("2026-03-13T12:00:00+00:00")
    assert "LIKE lower" in captured["sql"]


@pytest.mark.asyncio
async def test_courses_service_bulk_create_inserts_all_rows(monkeypatch) -> None:
    service = CoursesService(repository=CoursesRepository())
    captured = {}

    async def fake_course_exists(course_id: int) -> bool:
        return course_id == 4

    async def fake_list_existing_volunteer_ids(volunteer_ids: list[int]) -> set[int]:
        return set(volunteer_ids)

    async def fake_list_existing_course_completion_volunteer_ids(
        *, course_id: int, volunteer_ids: list[int], semester_code: int
    ) -> set[int]:
        captured["semester_code"] = semester_code
        return set()

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [101, 102]

    class FakeSession:
        async def execute(self, stmt, params=None):
            captured["params"] = params
            return FakeResult()

    monkeypatch.setattr(service.repository, "course_exists", fake_course_exists)
    monkeypatch.setattr(
        service.repository,
        "list_existing_volunteer_ids",
        fake_list_existing_volunteer_ids,
    )
    monkeypatch.setattr(
        service.repository,
        "list_existing_course_completion_volunteer_ids",
        fake_list_existing_course_completion_volunteer_ids,
    )

    token = set_request_session(FakeSession())
    try:
        created_count = await service.create_course_completions(course_id=4, volunteer_ids=[12, 13], year=2026, term=1)
    finally:
        reset_request_session(token)

    assert created_count == 2
    assert captured["semester_code"] == 20261
    assert captured["params"] == [
        {"volunteer_id": 12, "course_id": 4, "completed_semester": 20261},
        {"volunteer_id": 13, "course_id": 4, "completed_semester": 20261},
    ]


@pytest.mark.asyncio
async def test_courses_service_bulk_create_rejects_duplicate_selected_volunteers() -> None:
    service = CoursesService(repository=CoursesRepository())

    with pytest.raises(InvalidCourseCompletionError):
        await service.create_course_completions(course_id=4, volunteer_ids=[12, 12], year=2026, term=1)


@pytest.mark.asyncio
async def test_courses_service_bulk_create_rejects_existing_same_semester_completion(
    monkeypatch,
) -> None:
    service = CoursesService(repository=CoursesRepository())

    async def fake_course_exists(course_id: int) -> bool:
        return True

    async def fake_list_existing_volunteer_ids(volunteer_ids: list[int]) -> set[int]:
        return set(volunteer_ids)

    async def fake_list_existing_course_completion_volunteer_ids(
        *, course_id: int, volunteer_ids: list[int], semester_code: int
    ) -> set[int]:
        return {13}

    monkeypatch.setattr(service.repository, "course_exists", fake_course_exists)
    monkeypatch.setattr(
        service.repository,
        "list_existing_volunteer_ids",
        fake_list_existing_volunteer_ids,
    )
    monkeypatch.setattr(
        service.repository,
        "list_existing_course_completion_volunteer_ids",
        fake_list_existing_course_completion_volunteer_ids,
    )

    with pytest.raises(DuplicateCourseCompletionError):
        await service.create_course_completions(course_id=4, volunteer_ids=[13, 14], year=2026, term=1)


@pytest.mark.asyncio
async def test_volunteers_service_plain_listing_uses_repository() -> None:
    repository = FakeVolunteersRepository(
        [
            {
                "id": 10016,
                "first_name": "Martin",
                "last_name": "Kleiven",
                "email": "placeholder@example.test",
                "phone": None,
                "birth_date": None,
                "created_at": "2026-03-13T12:00:00+00:00",
                "sha1": None,
                "filetype": None,
                "last_semester": None,
                "pingvin_points": 0,
            }
        ]
    )
    service = VolunteersService(repository=cast(VolunteersRepository, repository))
    service.list_volunteer_page_rows = repository.list_volunteers_page  # type: ignore[method-assign]

    rows = await service.list_volunteers_page(query=None, limit=10, cursor=None)

    assert rows.items[0].full_name == "Martin Kleiven"
    assert repository.calls == [
        {
            "method": "list_volunteers_page",
            "limit": 11,
            "after_last_name": None,
            "after_first_name": None,
            "after_volunteer_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_volunteers_service_search_options_are_minimal_and_sorted(
    monkeypatch,
) -> None:
    service = VolunteersService()

    async def fake_list_volunteers(query: str | None = None, limit: int = 50):
        assert query == "sample"
        assert limit == 4
        return [
            type("Volunteer", (), {"volunteer_id": 14, "full_name": "Zeta Person"})(),
            type("Volunteer", (), {"volunteer_id": 12, "full_name": "Alpha Person"})(),
        ]

    monkeypatch.setattr(service, "list_volunteers", fake_list_volunteers)

    items = await service.list_volunteer_search_options("sample", limit=2)

    assert [(item.volunteer_id, item.full_name) for item in items] == [
        (12, "Alpha Person"),
        (14, "Zeta Person"),
    ]


@pytest.mark.asyncio
async def test_volunteers_service_search_queries_use_ranked_database_path(
    monkeypatch,
) -> None:
    service = VolunteersService()

    async def fake_search(
        normalized_query: str,
        limit: int,
        cursor: str | None,
        *,
        only_active: bool = False,
        include_total: bool = False,
    ):
        assert normalized_query == "martin kleiven"
        assert limit == 10
        assert cursor is None
        return "sentinel"

    monkeypatch.setattr(service, "_search_volunteers_page", fake_search)

    result = await service.list_volunteers_page(query="  Martin   Kleiven ", limit=10, cursor=None)

    assert result == "sentinel"


@pytest.mark.asyncio
async def test_volunteer_applications_pending_count_is_cached() -> None:
    repository = FakeVolunteerApplicationsRepository()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(
            app_secret_key="test-secret",
            app_public_base_url="https://personal.kvarteret.no",
        ),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
        pending_count_cache_ttl_seconds=60,
    )

    first = await service.count_pending_volunteer_applications()
    second = await service.count_pending_volunteer_applications()

    assert first == 3
    assert second == 3
    assert repository.count_calls == 1


@pytest.mark.asyncio
async def test_public_prospect_resolves_any_configured_group_slug() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.public_prospect_groups = {
        "debatt": PublicProspectGroup(
            group_id=81,
            slug="debatt",
            name="Debattkomiteen",
        ),
        "fest": PublicProspectGroup(
            group_id=82,
            slug="fest",
            name="Festkomiteen",
        ),
    }
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )

    await service.create_public_prospect_registration_record(
        PublicProspectRegistrationInput(
            full_name="Kari Nordmann",
            email="kari@example.test",
            phone="41234567",
            study_institution="UiB",
            background_details=None,
            first_choice_group_slug="debatt",
            second_choice_group_slug="fest",
        )
    )

    created = repository.created_public_prospects[0]
    assert created["first_choice_group_id"] == 81
    assert created["second_choice_group_id"] is None
    assert repository.created_public_prospects[1]["first_choice_group_id"] == 82
    assert created["first_choice_label"] == "Debattkomiteen"
    assert created["second_choice_label"] is None
    assert repository.created_public_prospects[1]["first_choice_label"] == "Festkomiteen"


@pytest.mark.asyncio
async def test_public_prospect_receives_bilingual_application_confirmation() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.public_prospect_groups = {
        "debatt": PublicProspectGroup(81, "debatt", "Debattkomiteen")
    }
    email_outbox = FakeEmailOutbox()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=email_outbox,
    )

    await service.create_public_prospect_registration(
        PublicProspectRegistrationInput(
            full_name="Kari Nordmann",
            email="kari@example.test",
            phone="41234567",
            study_institution="UiB",
            background_details=None,
            first_choice_group_slug="debatt",
            second_choice_group_slug=None,
        )
    )

    assert len(email_outbox.requests) == 1
    assert_email_enqueued(
        email_outbox.requests[0],
        recipient_email="kari@example.test",
        template_key=APPLICANT_APPLICATION_RECEIVED,
    )


@pytest.mark.asyncio
async def test_public_bar_choices_preserve_labels_and_route_only_primary_choice() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.public_prospect_groups = {
        "skjenke-gruppen": PublicProspectGroup(
            group_id=81,
            slug="skjenke-gruppen",
            name="Skjenkegruppen",
        ),
    }
    repository.public_prospect_role_ids[(81, "Halvtimen-skjenker")] = 17
    repository.public_prospect_role_ids[(81, "Pubdyr")] = 18
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )

    await service.create_public_prospect_registration_record(
        PublicProspectRegistrationInput(
            full_name="Kari Nordmann",
            email="kari@example.test",
            phone="41234567",
            study_institution="UiB",
            background_details=None,
            first_choice_group_slug="halvtimen",
            second_choice_group_slug="grondahls",
        )
    )

    created = repository.created_public_prospects[0]
    assert created["first_choice_group_id"] == 81
    assert created["second_choice_group_id"] is None
    assert repository.created_public_prospects[1]["first_choice_group_id"] == 81
    assert created["first_choice_label"] == "Halvtimen"
    assert created["second_choice_label"] is None
    assert repository.created_public_prospects[1]["first_choice_label"] == "Grøndahls"
    assert created["initial_group_id"] == 81
    assert created["initial_role_id"] == 17


@pytest.mark.asyncio
async def test_public_quiz_choice_routes_to_quiz_and_preserves_quiz_label() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.public_prospect_groups = {
        "quiz": PublicProspectGroup(
            group_id=298,
            slug="quiz",
            name="Quiz",
        ),
    }
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )

    await service.create_public_prospect_registration_record(
        PublicProspectRegistrationInput(
            full_name="Kari Nordmann",
            email="kari@example.test",
            phone="41234567",
            study_institution="UiB",
            background_details=None,
            first_choice_group_slug="quiz-gruppen",
            second_choice_group_slug=None,
        )
    )

    created = repository.created_public_prospects[0]
    assert created["first_choice_group_id"] == 298
    assert created["first_choice_label"] == "Quiz-gruppen"
    assert created["initial_group_id"] == created["first_choice_group_id"]
    assert created["initial_role_id"] is None


@pytest.mark.asyncio
async def test_public_prospect_rejects_an_unknown_group_slug() -> None:
    repository = FakeVolunteerApplicationsRepository()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )

    with pytest.raises(
        VolunteerApplicationValidationError,
        match="valgte grupper finnes ikke",
    ):
        await service.create_public_prospect_registration_record(
            PublicProspectRegistrationInput(
                full_name="Kari Nordmann",
                email="kari@example.test",
                phone="41234567",
                study_institution="UiB",
                background_details=None,
                first_choice_group_slug="ukjent",
                second_choice_group_slug=None,
            )
        )

    assert repository.created_public_prospects == []


@pytest.mark.asyncio
async def test_public_prospect_allows_friend_who_is_already_volunteer() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.existing_volunteer_ids_by_email = {"friend@example.test": 10232}
    repository.public_prospect_groups = {
        "debatt": PublicProspectGroup(81, "debatt", "Debattkomiteen")
    }
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )

    await service.create_public_prospect_registration_record(
        PublicProspectRegistrationInput(
            full_name="Kari Nordmann",
            email="kari@example.test",
            phone="41234567",
            study_institution="UiB",
            background_details=None,
            first_choice_group_slug="debatt",
            second_choice_group_slug=None,
            friend_emails=["friend@example.test"],
        )
    )
    assert repository.created_public_prospects[0]["friend_invites"][0][0] == "friend@example.test"

@pytest.mark.asyncio
async def test_public_prospect_allows_friend_with_another_application() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.active_registration_ids_by_email = {"friend@example.test": 77}
    repository.public_prospect_groups = {
        "debatt": PublicProspectGroup(81, "debatt", "Debattkomiteen")
    }
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )

    await service.create_public_prospect_registration_record(
        PublicProspectRegistrationInput(
            full_name="Kari Nordmann",
            email="kari@example.test",
            phone="41234567",
            study_institution="UiB",
            background_details=None,
            first_choice_group_slug="debatt",
            second_choice_group_slug=None,
            friend_emails=["friend@example.test"],
        )
    )
    assert repository.created_public_prospects[0]["friend_invites"][0][0] == "friend@example.test"

@pytest.mark.asyncio
async def test_volunteer_applications_recent_registrations_are_individual() -> None:
    repository = FakeVolunteerApplicationsRepository()
    inviter_row = {
        "id": 21,
        "first_name": "Inviter",
        "last_name": "Person",
        "email": "inviter@example.com",
        "phone": "11111111",
        "created_at": datetime(2026, 5, 21, tzinfo=UTC),
        "latest_group_name": "Skjenkegruppen",
        "latest_role_name": None,
        "latest_semester_code": 20261,
        "photo_sha1": None,
        "photo_filetype": None,
        "registration_id": 31,
    }
    invitee_row = {
        "id": 22,
        "first_name": "Invitee",
        "last_name": "Person",
        "email": "invitee@example.com",
        "phone": "22222222",
        "created_at": datetime(2026, 5, 21, tzinfo=UTC),
        "latest_group_name": "Skjenkegruppen",
        "latest_role_name": None,
        "latest_semester_code": 20261,
        "photo_sha1": None,
        "photo_filetype": None,
        "registration_id": 32,
    }
    repository.recent_registration_rows = [invitee_row, inviter_row]
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
        pending_count_cache_ttl_seconds=60,
    )
    service.list_recent_volunteer_registrations = (  # type: ignore[method-assign]
        repository.list_recent_volunteer_registrations
    )

    page = await service.list_recent_volunteer_registrations_page()

    assert [item.full_name for item in page.items] == [
        "Invitee Person",
        "Inviter Person",
    ]


@pytest.mark.asyncio
async def test_volunteer_applications_submit_invalidates_pending_count_cache() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.application_photo_sha1 = "abc123"
    repository.application_photo_filetype = "jpg"
    repository.application_photo_url = "/media/photos/abc123.jpg?token=test"
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
        pending_count_cache_ttl_seconds=60,
    )

    await service.count_pending_volunteer_applications()
    repository.pending_count = 4
    detail = await service.submit_volunteer_application(
        "token-123",
        VolunteerApplicationSubmissionInput(
            first_name="Ada",
            last_name="Lovelace",
            phone="+4799999998",
            birth_date=date(1815, 12, 10),
            gender="K",
            address="Adresse 1",
            postal_code="5000",
        ),
    )
    refreshed = await service.count_pending_volunteer_applications()

    assert detail.registration_id == 7
    assert refreshed == 4
    assert repository.saved_registration_ids == [7]
    assert repository.saved_submission_phones == ["+4799999998"]
    assert repository.count_calls == 2


@pytest.mark.asyncio
async def test_volunteer_applications_submit_does_not_enqueue_email() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.application_photo_sha1 = "abc123"
    repository.application_photo_filetype = "jpg"
    repository.application_photo_url = "/media/photos/abc123.jpg?token=test"
    email_outbox = FakeEmailOutbox()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(
            app_secret_key="test-secret",
            app_public_base_url="https://personal.kvarteret.no",
        ),
        repository=repository,
        email_outbox=email_outbox,
        pending_count_cache_ttl_seconds=60,
    )

    await service.submit_volunteer_application(
        "token-123",
        VolunteerApplicationSubmissionInput(
            first_name="Ada",
            last_name="Lovelace",
            phone="+4799999998",
            birth_date=date(1815, 12, 10),
            gender="K",
            address="Adresse 1",
            postal_code="5000",
        ),
        base_url="https://personal.kvarteret.no",
    )

    assert email_outbox.requests == []


@pytest.mark.asyncio
async def test_volunteer_applications_submit_normalizes_local_phone_number() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.application_photo_sha1 = "abc123"
    repository.application_photo_filetype = "jpg"
    repository.application_photo_url = "/media/photos/abc123.jpg?token=test"
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
        pending_count_cache_ttl_seconds=60,
    )

    await service.submit_volunteer_application(
        "token-123",
        VolunteerApplicationSubmissionInput(
            first_name="Ada",
            last_name="Lovelace",
            phone="95230903",
            birth_date=date(1815, 12, 10),
            gender="K",
            address="Adresse 1",
            postal_code="5000",
        ),
    )

    assert repository.saved_submission_phones == ["+4795230903"]


@pytest.mark.asyncio
async def test_volunteer_applications_submit_rejects_invalid_phone_number() -> None:
    repository = FakeVolunteerApplicationsRepository()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
        pending_count_cache_ttl_seconds=60,
    )

    with pytest.raises(VolunteerApplicationValidationError):
        await service.submit_volunteer_application(
            "token-123",
            VolunteerApplicationSubmissionInput(
                first_name="Ada",
                last_name="Lovelace",
                phone="123",
                birth_date=date(1815, 12, 10),
                gender="K",
                address="Adresse 1",
                postal_code="5000",
            ),
        )


@pytest.mark.asyncio
async def test_volunteer_applications_submit_requires_profile_photo_when_missing() -> None:
    repository = FakeVolunteerApplicationsRepository()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
        pending_count_cache_ttl_seconds=60,
    )

    with pytest.raises(VolunteerApplicationValidationError, match="Profilbilde er påkrevd."):
        await service.submit_volunteer_application(
            "token-123",
            VolunteerApplicationSubmissionInput(
                first_name="Ada",
                last_name="Lovelace",
                phone="+4799999998",
                birth_date=date(1815, 12, 10),
                gender="K",
                address="Adresse 1",
                postal_code="5000",
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("birth_date", "address", "postal_code", "message"),
    [
        (None, "Adresse 1", "5000", "Birth date is required."),
        (date(1815, 12, 10), " ", "5000", "Address is required."),
        (
            date(1815, 12, 10),
            "Adresse 1",
            None,
            "Postal code must be exactly 4 digits",
        ),
    ],
)
async def test_volunteer_applications_submit_requires_complete_profile_fields(
    birth_date, address, postal_code, message
) -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.application_photo_sha1 = "abc123"
    repository.application_photo_filetype = "jpg"
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )

    with pytest.raises(VolunteerApplicationValidationError, match=message):
        await service.submit_volunteer_application(
            "token-123",
            VolunteerApplicationSubmissionInput(
                first_name="Ada",
                last_name="Lovelace",
                phone="+4799999998",
                birth_date=birth_date,
                gender="K",
                address=address,
                postal_code=postal_code,
            ),
        )


@pytest.mark.asyncio
async def test_volunteer_applications_approve_invalidates_pending_count_cache() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.detail_status = "trial"
    repository.application_photo_sha1 = "abc123"
    repository.application_photo_filetype = "jpg"
    email_outbox = FakeEmailOutbox()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(
            app_secret_key="test-secret",
            app_public_base_url="https://personal.kvarteret.no",
        ),
        repository=repository,
        email_outbox=email_outbox,
        pending_count_cache_ttl_seconds=60,
    )

    await service.count_pending_volunteer_applications()
    repository.pending_count = 2
    volunteer_id = await service.approve_volunteer_application(7)
    refreshed = await service.count_pending_volunteer_applications()

    assert volunteer_id == 12
    assert refreshed == 2
    assert repository.approved_registration_ids == [7]
    assert repository.count_calls == 2
    assert email_outbox.requests == []


@pytest.mark.asyncio
async def test_starting_trial_enqueues_profile_completion_email() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.detail_status = "contacted"
    email_outbox = FakeEmailOutbox()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(
            app_secret_key="test-secret",
            app_public_base_url="https://personal.kvarteret.no",
        ),
        repository=repository,
        email_outbox=email_outbox,
    )

    detail = await service.start_trial(7)

    assert detail.status == "trial"
    assert detail.promoted_volunteer_id == 12
    assert repository.promoted_volunteer_id == 12
    assert service.volunteer_creator.created[0]["contract_signed"] is False  # type: ignore[attr-defined]
    assert_email_enqueued(
        email_outbox.requests[0],
        recipient_email="registrant@example.com",
        template_key=APPLICANT_PROFILE_COMPLETION,
    )


@pytest.mark.asyncio
async def test_approving_trial_reuses_trial_volunteer_record() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.detail_status = "contacted"
    repository.application_photo_sha1 = "abc123"
    repository.application_photo_filetype = "jpg"
    creator = FakeVolunteerCreator()
    service = VolunteerApplicationsService(
        volunteer_creator=creator,
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )

    await service.start_trial(7)
    repository.detail_status = "trial"

    volunteer_id = await service.approve_volunteer_application(7)

    assert volunteer_id == 12
    assert len(creator.created) == 2
    assert creator.created[0]["contract_signed"] is False
    assert repository.trial_assignment_id == 34
    assert creator.created[1]["assignment_id"] == 34
    assert creator.created[1]["volunteer_id"] == 12
    assert creator.created[1]["contract_signed"] is True


@pytest.mark.asyncio
async def test_approving_trial_rejects_incomplete_profile() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.detail_status = "trial"
    repository.application_submitted = False
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )

    with pytest.raises(
        VolunteerApplicationConflictError,
        match="full profile is complete",
    ):
        await service.approve_volunteer_application(7)

    assert repository.approved_registration_ids == []


@pytest.mark.asyncio
async def test_volunteer_applications_delete_invalidates_pending_count_cache() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.application_submitted = False
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
        pending_count_cache_ttl_seconds=60,
    )

    await service.count_pending_volunteer_applications()
    repository.pending_count = 1
    await service.delete_volunteer_application(7)
    refreshed = await service.count_pending_volunteer_applications()

    assert refreshed == 1
    assert repository.deleted_registration_ids == [7]


@pytest.mark.asyncio
async def test_volunteer_applications_service_enqueues_email_when_creating_invitation() -> None:
    repository = FakeVolunteerApplicationsRepository()
    email_outbox = FakeEmailOutbox()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(
            app_secret_key="test-secret",
            app_public_base_url="https://personal.kvarteret.no",
        ),
        repository=repository,
        email_outbox=email_outbox,
        pending_count_cache_ttl_seconds=60,
    )

    invite = await service.create_volunteer_application_invitation(
        "new@example.test",
        initial_group_id=3,
        initial_role_id=9,
    )

    assert invite.email == "new@example.test"
    assert repository.created_invites[0]["email"] == "new@example.test"
    assert_email_enqueued(
        email_outbox.requests[0],
        recipient_email="new@example.test",
        template_key=APPLICANT_INVITATION,
    )


@pytest.mark.asyncio
async def test_volunteer_applications_service_can_resend_invitation_email() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.detail_status = "new"
    email_outbox = FakeEmailOutbox()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(
            app_secret_key="test-secret",
            app_public_base_url="https://personal.kvarteret.no",
        ),
        repository=repository,
        email_outbox=email_outbox,
        pending_count_cache_ttl_seconds=60,
    )

    detail = await service.resend_volunteer_application_invitation(7)

    assert detail.registration_id == 7
    assert_email_enqueued(
        email_outbox.requests[0],
        recipient_email="registrant@example.com",
        template_key=APPLICANT_INVITATION,
    )


@pytest.mark.asyncio
async def test_volunteer_applications_service_enqueues_with_explicit_base_url() -> None:
    repository = FakeVolunteerApplicationsRepository()
    email_outbox = FakeEmailOutbox()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=email_outbox,
        pending_count_cache_ttl_seconds=60,
    )

    await service.create_volunteer_application_invitation(
        "new@example.test",
        base_url="http://localhost:8000",
    )

    assert_email_enqueued(
        email_outbox.requests[0],
        recipient_email="new@example.test",
        template_key=APPLICANT_INVITATION,
    )


@pytest.mark.asyncio
async def test_volunteer_applications_service_allows_existing_volunteer_invitation() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.existing_volunteer_ids_by_email = {"existing@example.test": 42}
    email_outbox = FakeEmailOutbox()
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(
            app_secret_key="test-secret",
            app_public_base_url="https://personal.kvarteret.no",
        ),
        repository=repository,
        email_outbox=email_outbox,
        pending_count_cache_ttl_seconds=60,
    )

    invite = await service.create_volunteer_application_invitation("existing@example.test")
    assert invite.email == "existing@example.test"
    assert len(repository.created_invites) == 1
    assert len(email_outbox.requests) == 1


@pytest.mark.asyncio
async def test_mobile_card_service_rate_limits_repeated_invalid_session_attempts() -> None:
    service = MobileCardService(
        Settings(
            app_secret_key="test-secret",
            mobile_card_session_attempt_limit=2,
            mobile_card_session_attempt_window_seconds=600,
        ),
        repository=FakeMobileCardRepository(),  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )

    with pytest.raises(MobileCardInvalidAccessCodeError):
        await service.create_session("person@example.com", "111111", source_key="127.0.0.1")
    with pytest.raises(MobileCardInvalidAccessCodeError):
        await service.create_session("person@example.com", "222222", source_key="127.0.0.1")
    with pytest.raises(MobileCardRateLimitedError):
        await service.create_session("person@example.com", "333333", source_key="127.0.0.1")


@pytest.mark.asyncio
@pytest.mark.parametrize("personal_login", [False, True])
async def test_mobile_card_service_sends_email_when_generating_access_code(personal_login: bool) -> None:
    repository = FakeMobileCardRepository(
        volunteers_by_email=[
            {
                "id": 12,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "code_hash": None,
                "created_at": None,
            }
        ]
    )
    email_sender = FakeEmailSender()
    service = MobileCardService(
        Settings(app_secret_key="test-secret", mobile_card_access_code_ttl_minutes=10),
        repository=repository,  # type: ignore[arg-type]
        email_sender=email_sender,
        rate_limiter=InMemoryRateLimiter(),
    )

    await service.request_access_code("person@example.com", personal_login=personal_login)

    assert len(repository.stored_access_codes) == 1
    volunteer_id, code_hash, created_at = repository.stored_access_codes[0]
    assert volunteer_id == 12
    assert created_at.tzinfo == UTC
    assert len(code_hash) == 64  # SHA-256 hex digest
    assert email_sender.sent_emails[0]["recipient_email"] == "person@example.com"
    expected_subject = "Din innlogging til Personal er klar" if personal_login else "Samfunnet i Bergen Internkort is ready for you"
    assert email_sender.sent_emails[0]["subject"] == expected_subject
    assert ("Din innlogging til Personal er klar" if personal_login else "Your verification code") in email_sender.sent_emails[0]["html_body"]
    import re

    assert re.search(r"\b\d{6}\b", email_sender.sent_emails[0]["html_body"]), "expected a 6-digit code in the email"
    assert ("Koden er gyldig i 10 minutter." if personal_login else "This code expires in 10 minutes.") in email_sender.sent_emails[0]["html_body"]
    assert ("Hvis du ikke ba om koden" if personal_login else "If you did not request this code") in email_sender.sent_emails[0]["html_body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("photo_path", ["trial.jpg", None])
async def test_trial_applicant_can_log_in_and_receives_temporary_card(photo_path) -> None:
    trial_ends_at = datetime.now(UTC) + timedelta(days=21)
    snapshot = TrialApplicantCardSnapshot(
        application_id=77,
        first_name="Ada",
        last_name="Lovelace",
        birth_date=None,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        trial_ends_at=trial_ends_at,
        photo_path=photo_path,
        group_name="Bar",
        role_name="Prøvefrivillig",
        discount_level=1,
        volunteer_id=12,
    )
    repository = FakeMobileCardRepository()
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
        media_token_service=FakeMediaTokenService(),  # type: ignore[arg-type]
        trial_applicant_provider=FakeTrialApplicantProvider(snapshot),
    )

    await service.request_access_code("trial@example.com")
    session = await service.create_session("trial@example.com", "123456")

    assert repository.stored_trial_access_codes[0][0] == 77
    assert session.card.person_id == 12
    assert session.card.valid_until == trial_ends_at
    assert session.card.photo_url == ("/media/photos/trial.jpg?token=test" if photo_path else None)
    assert session.card.active_roles[0].name == "Prøvefrivillig (Prøvetid)"
    assert session.card.active_roles[0].discount_level == 1
    assert session.card.active_roles[0].signed_contract is False


@pytest.mark.asyncio
async def test_expired_trial_applicant_gets_code_and_card_flagged_expired() -> None:
    snapshot = TrialApplicantCardSnapshot(
        application_id=77,
        first_name="Ada",
        last_name="Lovelace",
        birth_date=None,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        trial_ends_at=datetime.now(UTC) - timedelta(days=1),
        photo_path="trial.jpg",
        group_name="Bar",
        role_name="Prøvefrivillig",
        discount_level=1,
    )
    repository = FakeMobileCardRepository()
    email_sender = FakeEmailSender()
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=email_sender,
        rate_limiter=InMemoryRateLimiter(),
        trial_applicant_provider=FakeTrialApplicantProvider(snapshot),
    )

    await service.request_access_code("trial@example.com")
    session = await service.create_session("trial@example.com", "123456")
    current = await service.get_current_card(session.session_token)

    assert len(email_sender.sent_emails) == 1
    for card in (session.card, current.card):
        assert card.probation_expired is True
        assert card.active_roles == []
        assert card.photo_url is None


@pytest.mark.asyncio
async def test_trial_session_is_rejected_after_trial_is_no_longer_active() -> None:
    snapshot = TrialApplicantCardSnapshot(
        application_id=77,
        first_name="Ada",
        last_name="Lovelace",
        birth_date=None,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        trial_ends_at=datetime.now(UTC) + timedelta(days=1),
        photo_path="trial.jpg",
        group_name="Bar",
        role_name="Prøvefrivillig",
        discount_level=1,
    )
    provider = FakeTrialApplicantProvider(snapshot)
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=FakeMobileCardRepository(),  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
        trial_applicant_provider=provider,
    )
    token = service.sessions.build_token({"trial_application_id": 77})
    provider.snapshot = None

    with pytest.raises(MobileCardInvalidAccessCodeError, match="expired"):
        await service.get_current_card(token)


@pytest.mark.asyncio
async def test_mobile_card_service_issues_new_code_on_every_request() -> None:
    """Every access-code request generates and stores a new code (cooldown reuse is dead)."""
    repository = FakeMobileCardRepository(
        volunteers_by_email=[
            {
                "id": 12,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "code_hash": "some-old-hash",
                "created_at": datetime.now(UTC),
            }
        ]
    )
    email_sender = FakeEmailSender()
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=email_sender,
        rate_limiter=InMemoryRateLimiter(),
    )

    await service.request_access_code("person@example.com")

    # A new code was stored (not empty), even though an old one existed.
    assert len(repository.stored_access_codes) == 1
    _, code_hash_new, _ = repository.stored_access_codes[0]
    assert code_hash_new != "some-old-hash"
    # The email contains a 6-digit code.
    import re

    assert re.search(r"\b\d{6}\b", email_sender.sent_emails[0]["html_body"])


@pytest.mark.asyncio
async def test_mobile_card_service_returns_fresh_card_without_renewal_when_token_is_new() -> None:
    repository = FakeMobileCardRepository(card_snapshot=_build_mobile_card_snapshot())
    service = MobileCardService(
        Settings(
            app_secret_key="test-secret",
            mobile_card_session_ttl_days=90,
            mobile_card_session_renewal_threshold_days=30,
        ),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )

    token = service.sessions.serializer.dumps({"person_id": 12})

    result = await service.get_current_card(token)

    assert isinstance(result, MobileCardCurrentCardResult)
    assert result.card.person_id == 12
    assert result.card.role_history is None
    assert result.renewed_session_token is None


@pytest.mark.asyncio
async def test_mobile_card_service_includes_role_history_when_requested() -> None:
    repository = FakeMobileCardRepository(card_snapshot=_build_mobile_card_snapshot())
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )

    token = service.sessions.serializer.dumps({"person_id": 12})

    result = await service.get_current_card(token, include_role_history=True)

    assert result.card.role_history is not None
    assert [role.name for role in result.card.role_history] == ["Shift lead", "Member"]
    assert result.card.role_history[0].semester == "Vår"
    assert result.card.role_history[1].semester == "Høst"


@pytest.mark.asyncio
async def test_mobile_card_service_keeps_real_photo_when_april_toggle_is_disabled() -> None:
    repository = FakeMobileCardRepository(
        card_snapshot=MobileCardSnapshot(
            volunteer_id=12,
            first_name="Ada",
            last_name="Lovelace",
            birth_date=None,
            created_at=datetime(2026, 3, 13, tzinfo=UTC),
            photo_path="abc123.jpg",
            pingvin_points=8,
            active_roles=[
                MobileCardRoleSnapshot(
                    name="Skjenker",
                    group="Skjenkegruppen",
                    group_id=253,
                    discount_level=2,
                    pingvin_points=4,
                    signed_contract=True,
                )
            ],
        )
    )
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
        media_token_service=FakeMediaTokenService(),  # type: ignore[arg-type]
        april_state_service=FakeMobileCardAprilStateService(False),
    )

    result = await service.get_current_card(service.sessions.serializer.dumps({"person_id": 12}))

    assert result.card.photo_url == "/media/photos/abc123.jpg?token=test"


@pytest.mark.asyncio
async def test_mobile_card_service_hides_photo_when_april_toggle_is_enabled() -> None:
    repository = FakeMobileCardRepository(card_snapshot=_build_mobile_card_snapshot())
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
        media_token_service=FakeMediaTokenService(),  # type: ignore[arg-type]
        april_state_service=FakeMobileCardAprilStateService(True),
    )

    result = await service.get_current_card(service.sessions.serializer.dumps({"person_id": 12}))

    assert result.card.photo_url is None


@pytest.mark.asyncio
async def test_mobile_card_service_hides_photo_without_media_service_in_april_mode() -> None:
    repository = FakeMobileCardRepository(
        card_snapshot=MobileCardSnapshot(
            volunteer_id=12,
            first_name="Ada",
            last_name="Lovelace",
            birth_date=None,
            created_at=datetime(2026, 3, 13, tzinfo=UTC),
            photo_path="abc123.jpg",
            pingvin_points=8,
            active_roles=[
                MobileCardRoleSnapshot(
                    name="Presse",
                    group="PR-etaten",
                    group_id=270,
                    discount_level=2,
                    pingvin_points=4,
                    signed_contract=True,
                ),
                MobileCardRoleSnapshot(
                    name="Utvikler",
                    group="E-tjenesten",
                    group_id=254,
                    discount_level=2,
                    pingvin_points=4,
                    signed_contract=True,
                ),
            ],
        )
    )
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
        april_state_service=FakeMobileCardAprilStateService(True),
    )

    result = await service.get_current_card(service.sessions.serializer.dumps({"person_id": 12}))

    assert result.card.photo_url is None


@pytest.mark.asyncio
async def test_mobile_card_service_hides_photo_for_unmapped_groups_in_april_mode() -> None:
    repository = FakeMobileCardRepository(
        card_snapshot=MobileCardSnapshot(
            volunteer_id=12,
            first_name="Ada",
            last_name="Lovelace",
            birth_date=None,
            created_at=datetime(2026, 3, 13, tzinfo=UTC),
            photo_path="abc123.jpg",
            pingvin_points=8,
            active_roles=[
                MobileCardRoleSnapshot(
                    name="Leder",
                    group="Administrasjonen",
                    group_id=263,
                    discount_level=2,
                    pingvin_points=4,
                    signed_contract=True,
                )
            ],
        )
    )
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
        april_state_service=FakeMobileCardAprilStateService(True),
    )

    result = await service.get_current_card(service.sessions.serializer.dumps({"person_id": 12}))

    assert result.card.photo_url is None


@pytest.mark.asyncio
async def test_mobile_card_service_renews_session_when_token_is_near_expiry(
    monkeypatch,
) -> None:
    import itsdangerous.timed

    repository = FakeMobileCardRepository(card_snapshot=_build_mobile_card_snapshot())
    service = MobileCardService(
        Settings(
            app_secret_key="test-secret",
            mobile_card_session_ttl_days=90,
            mobile_card_session_renewal_threshold_days=30,
        ),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )

    now_timestamp = itsdangerous.timed.time.time()
    sixty_five_days_in_seconds = 65 * 24 * 3600
    monkeypatch.setattr(
        itsdangerous.timed.time,
        "time",
        lambda: now_timestamp - sixty_five_days_in_seconds,
    )
    token = service.sessions.serializer.dumps({"person_id": 12})
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: now_timestamp)

    result = await service.get_current_card(token)

    assert result.card.person_id == 12
    assert result.renewed_session_token is not None
    assert result.renewed_session_token != token


@pytest.mark.asyncio
async def test_mobile_card_service_reports_expired_token_reason(monkeypatch) -> None:
    import itsdangerous.timed

    repository = FakeMobileCardRepository(card_snapshot=_build_mobile_card_snapshot())
    service = MobileCardService(
        Settings(app_secret_key="test-secret", mobile_card_session_ttl_days=1),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )

    now_timestamp = itsdangerous.timed.time.time()
    two_days_in_seconds = 2 * 24 * 3600
    monkeypatch.setattr(
        itsdangerous.timed.time,
        "time",
        lambda: now_timestamp - two_days_in_seconds,
    )
    token = service.sessions.serializer.dumps({"person_id": 12})
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: now_timestamp)

    with pytest.raises(MobileCardInvalidSessionError) as exc_info:
        await service.get_current_card(token)

    assert exc_info.value.reason == "expired"


@pytest.mark.asyncio
async def test_mobile_card_service_reports_bad_signature_reason() -> None:
    repository = FakeMobileCardRepository(card_snapshot=_build_mobile_card_snapshot())
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )
    other_service = MobileCardService(
        Settings(app_secret_key="other-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )

    token = other_service.sessions.serializer.dumps({"person_id": 12})

    with pytest.raises(MobileCardInvalidSessionError) as exc_info:
        await service.get_current_card(token)

    assert exc_info.value.reason == "bad_signature"


@pytest.mark.asyncio
async def test_mobile_card_service_reports_malformed_reason() -> None:
    repository = FakeMobileCardRepository(card_snapshot=_build_mobile_card_snapshot())
    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=repository,  # type: ignore[arg-type]
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )

    token = service.sessions.serializer.dumps({"review": False})

    with pytest.raises(MobileCardInvalidSessionError) as exc_info:
        await service.get_current_card(token)

    assert exc_info.value.reason == "malformed"


@pytest.mark.parametrize(
    ("now", "expected_word"),
    [
        (datetime(2026, 3, 2, 3, 59, 59, tzinfo=UTC), "korpingvin"),
        (datetime(2026, 3, 2, 4, 0, 0, tzinfo=UTC), "keiserpingvin"),
        (datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC), "vinpingvin"),
        (datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC), "kaffepingvin"),
    ],
)
def test_mobile_card_word_of_the_day_is_stable_for_the_effective_day(now: datetime, expected_word: str) -> None:
    assert _word_of_the_day(now) == expected_word


@pytest.mark.asyncio
async def test_rejected_trial_cannot_be_restored_as_permanent_volunteer() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.detail_status = "not_volunteer"
    repository.promoted_volunteer_id = 12
    repository.application_submitted = False
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )
    with pytest.raises(VolunteerApplicationConflictError, match="Only previously promoted"):
        await service.restore_volunteer_application(7)
    assert repository.detail_status == "not_volunteer"


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_restoring_previously_approved_volunteer_requires_complete_profile(complete) -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.detail_status = "not_volunteer"
    repository.promoted_volunteer_id = 12
    repository.promoted_at = datetime.now(UTC)
    repository.application_submitted = complete
    repository.application_photo_sha1 = "photo"
    repository.application_photo_filetype = "jpg"
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )
    if complete:
        detail = await service.restore_volunteer_application(7)
        assert detail.status == "volunteer"
    else:
        with pytest.raises(VolunteerApplicationConflictError, match="full profile"):
            await service.restore_volunteer_application(7)
        assert repository.detail_status == "not_volunteer"


@pytest.mark.asyncio
async def test_admin_invitation_reuses_existing_same_target() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.active_targets = {("existing@example.com", None, None): 7}
    service = VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(), settings=Settings(app_secret_key="test"),
        repository=repository, email_outbox=FakeEmailOutbox(),
    )
    invite = await service.create_volunteer_application_invitation(" Existing@Example.com ")
    assert invite.registration_id == 7
    assert repository.created_invites == []
    assert repository.locked_emails == [["existing@example.com"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate_type", ["volunteer", "application"])
async def test_start_trial_allows_existing_person_or_other_target(duplicate_type):
    repository = FakeVolunteerApplicationsRepository()
    repository.detail_status = "contacted"
    creator = FakeVolunteerCreator()
    if duplicate_type == "volunteer":
        repository.existing_volunteer_ids_by_email["registrant@example.com"] = 55
    else:
        repository.active_registration_ids_by_email["registrant@example.com"] = 8
    service = VolunteerApplicationsService(
        volunteer_creator=creator, settings=Settings(app_secret_key="test"),
        repository=repository, email_outbox=FakeEmailOutbox(),
    )
    detail = await service.start_trial(7)
    assert detail.status == "trial"
    assert len(creator.created) == 1
    if duplicate_type == "volunteer":
        assert creator.created[0]["volunteer_id"] == 55
        assert "first_name" not in creator.created[0]
        assert detail.owns_volunteer_profile is False
    else:
        assert detail.owns_volunteer_profile is True


@pytest.mark.asyncio
async def test_approval_requires_a_role_on_server() -> None:
    repository = FakeVolunteerApplicationsRepository()
    repository.detail_status = "trial"
    repository.initial_role_id = None
    repository.application_photo_sha1 = "photo"
    repository.application_photo_filetype = "jpg"
    creator = FakeVolunteerCreator()
    service = VolunteerApplicationsService(
        volunteer_creator=creator, settings=Settings(app_secret_key="test"),
        repository=repository, email_outbox=FakeEmailOutbox(),
    )
    with pytest.raises(VolunteerApplicationConflictError, match="Choose a role"):
        await service.approve_volunteer_application(7)
    assert creator.created == []
    assert repository.approved_registration_ids == []


@pytest.mark.asyncio
async def test_trial_session_is_exchanged_for_permanent_session_after_approval() -> None:
    provider = FakeTrialApplicantProvider(None)
    provider.approved_volunteer_id = 12
    service = MobileCardService(
        Settings(app_secret_key="test"),
        repository=FakeMobileCardRepository(card_snapshot=_build_mobile_card_snapshot()),
        email_sender=FakeEmailSender(), rate_limiter=InMemoryRateLimiter(),
        trial_applicant_provider=provider,
    )
    token = service.sessions.build_token({"trial_application_id": 77})
    result = await service.get_current_card(token, include_role_history=True)
    assert result.card.person_id == 12
    assert result.card.active_roles[0].signed_contract is True
    assert result.card.role_history
    assert result.renewed_session_token
    decoded = service.sessions.decode_token(result.renewed_session_token)
    assert decoded.person_id == 12
    assert decoded.trial_application_id is None
    permanent = await service.get_current_card(result.renewed_session_token)
    assert permanent.card.person_id == 12


@pytest.mark.asyncio
async def test_mobile_session_identity_connects_logout_by_diagnostic_session(caplog):
    import logging
    from app.observability import bind_request_context, reset_request_context, get_logger

    service = MobileCardService(
        Settings(app_secret_key="test-secret"),
        repository=FakeMobileCardRepository(
            volunteer_by_email_and_code={"id": 12},
            card_snapshot=_build_mobile_card_snapshot(),
        ),
        email_sender=FakeEmailSender(),
        rate_limiter=InMemoryRateLimiter(),
    )
    token = bind_request_context(session_id="a" * 32, request_id="login-request")
    try:
        with caplog.at_level(logging.INFO):
            await service.create_session("person@example.com", "123456")
    finally:
        reset_request_context(token)
    token = bind_request_context(session_id="a" * 32, request_id="logout-request")
    try:
        with caplog.at_level(logging.WARNING):
            get_logger("app.audit").warning("mobile_card.session.invalidated", extra={"failure_stage": "reauthorization"})
    finally:
        reset_request_context(token)
    login = next(r for r in caplog.records if getattr(r, "event", "") == "mobile_card.session.created")
    logout = next(r for r in caplog.records if getattr(r, "event", "") == "mobile_card.session.invalidated")
    assert login.event_data["volunteer_id"] == 12
    assert login.event_data["subject_type"] == "volunteer"
    assert login.event_data["session_id"] == logout.event_data["session_id"]
    assert login.event_data["request_id"] != logout.event_data["request_id"]
    assert "volunteer_id" not in logout.event_data
    assert "person@example.com" not in str(login.event_data)


class _ConcurrentlyChangedRepository(FakeVolunteerApplicationsRepository):
    """Simulates another request moving the application while this one waits for the lock."""

    def __init__(self, *, changed_to: str) -> None:
        super().__init__()
        self.changed_to = changed_to

    async def lock_registration_emails(self, emails: list[str]) -> None:
        await super().lock_registration_emails(emails)
        self.detail_status = self.changed_to


def _service_for(repository: FakeVolunteerApplicationsRepository) -> VolunteerApplicationsService:
    return VolunteerApplicationsService(
        volunteer_creator=FakeVolunteerCreator(),
        settings=Settings(app_secret_key="test-secret"),
        repository=repository,
        email_outbox=FakeEmailOutbox(),
    )


@pytest.mark.asyncio
async def test_status_change_conflicts_when_application_changed_concurrently() -> None:
    repository = _ConcurrentlyChangedRepository(changed_to="contacted")
    repository.detail_status = "new"

    with pytest.raises(VolunteerApplicationConflictError, match="changed by someone else"):
        await _service_for(repository).reject_volunteer_application(7)

    assert repository.detail_status == "contacted"


@pytest.mark.asyncio
async def test_delete_conflicts_when_application_left_new_concurrently() -> None:
    repository = _ConcurrentlyChangedRepository(changed_to="contacted")
    repository.detail_status = "new"
    repository.application_submitted = False

    with pytest.raises(VolunteerApplicationConflictError, match="changed by someone else"):
        await _service_for(repository).delete_volunteer_application(7)

    assert repository.deleted_registration_ids == []


@pytest.mark.asyncio
async def test_refresh_token_renews_session_even_after_secret_rotation() -> None:
    snapshot = MobileCardSnapshot(
        volunteer_id=12,
        first_name="Ada",
        last_name="Lovelace",
        birth_date=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        photo_path=None,
        pingvin_points=0,
        active_roles=[],
    )
    repository = FakeMobileCardRepository(
        volunteer_by_email_and_code={"id": 12}, card_snapshot=snapshot
    )

    def make(secret: str) -> MobileCardService:
        return MobileCardService(
            Settings(app_secret_key=secret),
            repository=repository,  # type: ignore[arg-type]
            email_sender=FakeEmailSender(),
            rate_limiter=InMemoryRateLimiter(),
        )

    created = await make("old-secret").create_session("a@example.com", "123456")
    assert created.refresh_token is not None

    refreshed = await make("rotated-secret").refresh_session(created.refresh_token)
    assert refreshed.card.person_id == 12
    assert make("rotated-secret").sessions.decode_token(refreshed.session_token).person_id == 12

    with pytest.raises(MobileCardInvalidAccessCodeError):
        await make("rotated-secret").refresh_session("not-a-real-token")
