from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
import pytest

from app.auth.roles import UserRole
from app.dependencies import (
    get_role_assignments_service,
    get_courses_service,
    get_groups_service,
    get_semester_transfer_service,
    get_volunteers_service,
)
from app.main import create_app
from app.domain.courses.service import (
    CourseCompletionItem,
    CourseCompletionNotFoundError,
    CourseDetail,
    CourseListItem,
    DuplicateCourseCompletionError,
    InvalidCourseCompletionError,
    RequiredGroupItem,
)
from app.domain.groups.service import (
    GroupDetail,
    GroupListItem,
    GroupMemberCount,
    GroupMemberItem,
    GroupPositionItem,
    GroupRoleDeleteBlockedError,
    OrgSemesterDetailed,
    GroupBreakdownItem,
    SemesterGroup,
    SemesterRetentionStats,
    SemesterStats,
)
from app.domain.role_assignments.semester_transfer import SemesterTransferCandidate, SemesterTransferPreview
from app.domain.volunteers.models import VolunteerSearchOption
from tests.support.helpers import make_authenticated_user, override_authenticated_user


class FakeGroupsService:
    def __init__(self) -> None:
        self.created_role = None
        self.updated_role = None
        self.updated_group = None
        self.deleted_role = None
        self.deleted_history = None
        self.archived_group = None

    async def list_groups(self, query: str | None = None, limit: int = 100) -> list[GroupListItem]:
        return [
            GroupListItem(7, "Bar", "Bar group", True, 20262, None, 2),
            GroupListItem(8, "Arkiv", "Archive group", False, 20242, None, 1),
        ]

    async def get_group_detail(self, group_id: int) -> GroupDetail | None:
        return GroupDetail(
            group_id=7,
            name="Bar",
            description="Bar group",
            active=True,
            active_until_semester=20262,
            active_until_label="Fall 2026",
            parent_group_id=None,
            discount_step=2,
            created_at=datetime(2026, 3, 13, tzinfo=UTC),
            recruitment_contact_email=None,
            positions=[
                GroupPositionItem(
                    role_id=3,
                    role_name="Shift lead",
                    pingvin_points=4,
                    assignment_count=1,
                    delete_blockers=["Vervet har medlemmer og kan ikke slettes."],
                )
            ],
            recent_members=[
                GroupMemberItem(
                    history_id=9,
                    volunteer_id=12,
                    volunteer_name="Sample Person",
                    photo_url="/media/photos/abc123.jpg?token=test",
                    role_name="Shift lead",
                    semester_code=20262,
                    semester_label="Fall 2026",
                    contract_signed=True,
                )
            ],
            delete_blockers=[],
        )

    async def create_group_role(self, group_id: int, *, role_name: str, pingvin_points: int) -> int | None:
        self.created_role = (group_id, role_name, pingvin_points)
        return 14

    async def update_group(
        self,
        group_id: int,
        *,
        name: str,
        description: str | None,
        active: bool,
        active_until_semester: int,
        parent_group_id: int | None,
        discount_step: int | None,
        recruitment_contact_email: str | None = None,
    ) -> bool:
        self.updated_group = (
            group_id,
            name,
            description,
            active,
            active_until_semester,
            parent_group_id,
            discount_step,
        )
        return True

    async def update_group_role(self, group_id: int, role_id: int, *, role_name: str, pingvin_points: int) -> bool:
        self.updated_role = (group_id, role_id, role_name, pingvin_points)
        return True

    async def delete_group_role(self, group_id: int, role_id: int) -> bool:
        self.deleted_role = (group_id, role_id)
        return True

    async def get_group_history_by_semester(
        self, group_id: int, *, expanded_semesters: int | None = None
    ) -> list[SemesterGroup]:
        self.history_expanded_semesters = expanded_semesters
        return [
            await self.get_group_history_semester(group_id, 20262),
            SemesterGroup(
                semester_code=20261,
                semester_label="Spring 2026",
                members=None,
                member_count=4,
            ),
        ]

    async def get_group_history_semester(
        self, group_id: int, semester_code: int
    ) -> SemesterGroup:
        member = GroupMemberItem(
            history_id=9,
            volunteer_id=12,
            volunteer_name="Sample Person",
            photo_url="/media/photos/abc123.jpg?token=test",
            role_name="Shift lead",
            semester_code=semester_code,
            semester_label="Fall 2026",
            contract_signed=True,
        )
        return SemesterGroup(
            semester_code=semester_code,
            semester_label="Fall 2026",
            members=[member],
            member_count=1,
        )

    async def get_group_semester_stats(self, group_id: int) -> list[SemesterStats]:
        return [
            SemesterStats(semester_code=20261, semester_label="Spring 2026", member_count=11),
            SemesterStats(semester_code=20262, semester_label="Fall 2026", member_count=14),
        ]

    async def get_group_retention_stats(self, group_id: int) -> list[SemesterRetentionStats]:
        return [
            SemesterRetentionStats(
                semester_code=20261,
                semester_label="Spring 2026",
                total_members=11,
                retained_from_prev=7,
                new_members=4,
                retained_to_next_same_group=5,
                retained_to_next_other_group=3,
                retained_to_next=8,
                churned=3,
            ),
            SemesterRetentionStats(
                semester_code=20262,
                semester_label="Fall 2026",
                total_members=14,
                retained_from_prev=8,
                new_members=6,
                retained_to_next_same_group=0,
                retained_to_next_other_group=0,
                retained_to_next=0,
                churned=14,
            ),
        ]

    async def get_org_stats_detailed(self) -> list[OrgSemesterDetailed]:
        return [
            OrgSemesterDetailed(
                semester_code=20262,
                semester_label="Fall 2026",
                unique_members=120,
                groups=[GroupBreakdownItem(group_name="Bar", member_count=14)],
            )
        ]

    async def get_current_group_member_counts(self) -> list[GroupMemberCount]:
        return [GroupMemberCount(group_id=7, group_name="Bar", member_count=14)]

    async def get_org_retention_stats(self) -> list[SemesterRetentionStats]:
        return await self.get_group_retention_stats(7)

    async def delete_group_history_entry(self, group_id: int, history_id: int) -> None:
        self.deleted_history = (group_id, history_id)

    async def archive_group(self, group_id: int) -> bool:
        self.archived_group = group_id
        return True


class FakeCoursesService:
    def __init__(self) -> None:
        self.created_completion = None
        self.deleted_completion = None

    async def list_courses(self, query: str | None = None, limit: int = 100) -> list[CourseListItem]:
        return [CourseListItem(4, "Fire safety", "Safety basics", datetime(2026, 3, 13, tzinfo=UTC))]

    async def get_course_detail(self, course_id: int) -> CourseDetail | None:
        return CourseDetail(
            course_id=4,
            name="Fire safety",
            description="Safety basics",
            created_at=datetime(2026, 3, 13, tzinfo=UTC),
            required_groups=[RequiredGroupItem(group_id=7, group_name="Bar")],
            recent_completions=[
                CourseCompletionItem(
                    completion_id=11,
                    volunteer_id=12,
                    volunteer_name="Sample Person",
                    completed_semester_code=20262,
                    completed_semester_label="Fall 2026",
                )
            ],
            delete_blockers=[],
        )

    async def create_course_completion(self, *, course_id: int, volunteer_id: int, year: int, term: int) -> int:
        if volunteer_id == 999:
            raise InvalidCourseCompletionError("Selected volunteer was not found.")
        if volunteer_id == 12 and year == 2026 and term == 2:
            raise DuplicateCourseCompletionError("Dette kurset er allerede registrert for valgt semester.")
        self.created_completion = (course_id, volunteer_id, year, term)
        return 21

    async def create_course_completions(self, *, course_id: int, volunteer_ids: list[int], year: int, term: int) -> int:
        if not volunteer_ids:
            raise InvalidCourseCompletionError("Velg minst én frivillig.")
        if len(set(volunteer_ids)) != len(volunteer_ids):
            raise InvalidCourseCompletionError("Den samme frivillige kan ikke velges flere ganger.")
        if 999 in volunteer_ids:
            raise InvalidCourseCompletionError("Selected volunteer was not found.")
        if 12 in volunteer_ids and year == 2026 and term == 2:
            raise DuplicateCourseCompletionError("Dette kurset er allerede registrert for valgt semester.")
        self.created_completion = (course_id, volunteer_ids, year, term)
        return len(volunteer_ids)

    async def delete_course_completion(self, *, course_id: int, completion_id: int) -> None:
        if completion_id != 11:
            raise CourseCompletionNotFoundError(f"Course completion {completion_id} was not found.")
        self.deleted_completion = (course_id, completion_id)


class FakeSemesterTransferService:
    async def preview_transfer(self, group_id: int, source_semester: int | None = None, target_semester: int | None = None):
        return SemesterTransferPreview(
            group_id=7,
            group_name="Bar",
            source_semester=20261,
            source_semester_label="Spring 2026",
            target_semester=20262,
            target_semester_label="Fall 2026",
            candidates=[
                SemesterTransferCandidate(
                    volunteer_id=12,
                    volunteer_name="Sample Person",
                    role_id=3,
                    role_name="Shift lead",
                    contract_signed=False,
                    source_history_id=9,
                )
            ],
        )

    async def apply_transfer(self, group_id: int, target_semester: int, entries):
        return 1


class FakeVolunteersService:
    def __init__(self) -> None:
        self.created_assignment = None
        self.last_search_option_query = None
        self.last_search_option_limit = None

    async def add_role_assignment(
        self,
        *,
        volunteer_id: int,
        group_id: int,
        role_id: int,
        year: int,
        term: int,
        contract_signed: bool,
    ) -> None:
        self.created_assignment = (volunteer_id, group_id, role_id, year, term, contract_signed)

    async def list_volunteer_search_options(self, query: str, limit: int = 10) -> list[VolunteerSearchOption]:
        self.last_search_option_query = query
        self.last_search_option_limit = limit
        if "sample" not in query.lower():
            return []
        return [
            VolunteerSearchOption(
                volunteer_id=12,
                full_name="Sample Person",
            )
        ]


def test_groups_and_courses_pages_render() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    app.dependency_overrides[get_groups_service] = lambda: FakeGroupsService()
    app.dependency_overrides[get_courses_service] = lambda: FakeCoursesService()
    app.dependency_overrides[get_semester_transfer_service] = lambda: FakeSemesterTransferService()
    app.dependency_overrides[get_volunteers_service] = lambda: FakeVolunteersService()
    app.dependency_overrides[get_role_assignments_service] = lambda: FakeVolunteersService()
    client = TestClient(app)

    groups_response = client.get("/groups")
    group_new_response = client.get("/groups/new")
    group_detail_response = client.get("/groups/7")
    group_stats_response = client.get("/groups/7/stats")
    group_history_response = client.get("/groups/7/history")
    semester_transfer_response = client.get("/groups/7/semester-transfer")
    courses_response = client.get("/courses")
    course_new_response = client.get("/courses/new")
    course_detail_response = client.get("/courses/4")

    assert groups_response.status_code == 200
    assert "Aktive grupper" in groups_response.text
    assert "Inaktive grupper" in groups_response.text
    assert 'href="/groups/new"' in groups_response.text
    assert "Bar group" in groups_response.text
    assert "Archive group" in groups_response.text
    assert group_new_response.status_code == 200
    assert "Opprett gruppe" in group_new_response.text
    assert "Tilbake til grupper" in group_new_response.text
    assert group_detail_response.status_code == 200
    assert "Aktive dette semesteret" in group_detail_response.text
    assert "Lagre gruppe" in group_detail_response.text
    assert "Arkiver gruppe" in group_detail_response.text
    assert "Lagre verv" in group_detail_response.text
    assert "Opprett verv" in group_detail_response.text
    assert "Legg til frivillig" in group_detail_response.text
    assert "volunteerPicker({" in group_detail_response.text
    assert "/volunteers/search/options/typeahead" in group_detail_response.text
    assert "Valgt frivillig" in group_detail_response.text
    assert 'action="/groups/7/role-assignments"' in group_detail_response.text
    assert "Flytt til nytt semester" in group_detail_response.text
    assert "Aktiv til semester" not in group_detail_response.text
    assert 'name="active_until_semester"' in group_detail_response.text
    assert "Vervet har medlemmer og kan ikke slettes." in group_detail_response.text
    assert "disabled" in group_detail_response.text
    assert 'hx-boost:inherited="true"' in group_detail_response.text
    assert "htmx-ext-preload" not in group_detail_response.text
    assert "/static/vendor/htmx-4.0.0.min.js?v=" in group_detail_response.text
    assert 'hx-swap:inherited="innerMorph"' in group_detail_response.text
    assert 'hx-select="#page-shell"' not in group_detail_response.text
    assert 'hx-target="#page-shell"' not in group_detail_response.text
    assert 'hx-get="/groups/7/stats"' in group_detail_response.text
    assert 'hx-get="/groups/7/history"' in group_detail_response.text
    assert 'id="group-stats-panel"' in group_detail_response.text
    assert 'id="group-history-panel"' in group_detail_response.text
    assert 'hx-disinherit=' not in group_detail_response.text
    assert 'href="/volunteers/12"' in group_detail_response.text
    assert 'src="/media/photos/abc123.jpg?token=test"' in group_detail_response.text
    assert 'hx-delete="/groups/7/history/9"' in group_detail_response.text
    assert '/groups/7/history/9?_method=DELETE' in group_detail_response.text
    assert group_stats_response.status_code == 200
    assert "Medlemsutvikling" in group_stats_response.text
    assert "Medlemmer per semester" in group_stats_response.text
    assert "Samme gruppe" in group_stats_response.text
    assert "Annen gruppe" in group_stats_response.text
    assert "Totalt beholdt" in group_stats_response.text
    assert group_history_response.status_code == 200
    assert "Gruppehistorikk" in group_history_response.text
    # Older semesters load their members only when expanded.
    assert 'hx-get="/groups/7/history/semesters/20261"' in group_history_response.text
    assert 'hx-trigger="toggle once"' in group_history_response.text
    assert "4 medlemmer" in group_history_response.text
    assert 'href="/volunteers/12"' in group_history_response.text
    assert 'src="/media/photos/abc123.jpg?token=test"' in group_history_response.text
    assert '/groups/7/history/9?_method=DELETE' in group_history_response.text
    assert semester_transfer_response.status_code == 200
    assert "Flytt til nytt semester" in semester_transfer_response.text
    assert "Kopier medlemmer fra Spring 2026 til" in semester_transfer_response.text
    assert courses_response.status_code == 200
    assert "Fire safety" in courses_response.text
    assert "Opprett kurs" in courses_response.text
    assert 'href="/courses/new"' in courses_response.text
    assert course_new_response.status_code == 200
    assert "Opprett kurs" in course_new_response.text
    assert "Tilbake til kurs" in course_new_response.text
    assert course_detail_response.status_code == 200
    assert "Kursfullføringer" in course_detail_response.text
    assert "Lagre kurs" in course_detail_response.text
    assert "Legg til frivillig" in course_detail_response.text
    assert "volunteerPicker({" in course_detail_response.text
    assert "/volunteers/search/options/typeahead" in course_detail_response.text
    assert "Valgte frivillige" in course_detail_response.text
    assert "/static/vendor/hx-alpine-compat-4.0.0.js?v=" in course_detail_response.text
    assert 'action="/courses/4/completions"' in course_detail_response.text
    assert '/courses/4/completions/11?_method=DELETE' in course_detail_response.text
    assert 'href="/groups/7"' in course_detail_response.text
    assert 'href="/volunteers/12"' in course_detail_response.text
    # The CSP build of Alpine is self-hosted and pinned.
    assert "/static/vendor/alpinejs-csp-3.17.4.min.js?v=" in course_detail_response.text
    assert "alpinejs@3.x.x" not in course_detail_response.text
    assert "js/volunteer-picker.js" in course_detail_response.text
    assert "js/client-error-reporting.js" in course_detail_response.text
    assert "js/htmx-error-feedback.js" in course_detail_response.text
    assert "Legg til</button>" in course_detail_response.text


def test_group_update_preserves_active_until_when_hidden_field_is_missing() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    groups_service = FakeGroupsService()
    app.dependency_overrides[get_groups_service] = lambda: groups_service
    client = TestClient(app)

    response = client.post(
        "/groups/7?_method=PATCH",
        data={
            "name": "Oppdatert Bar",
            "description": "Ny beskrivelse",
            "active": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/groups/7"
    assert groups_service.updated_group == (
        7,
        "Oppdatert Bar",
        "Ny beskrivelse",
        True,
        20262,
        None,
        None,
    )


def test_group_role_actions_redirect_and_call_service() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    groups_service = FakeGroupsService()
    app.dependency_overrides[get_groups_service] = lambda: groups_service
    client = TestClient(app)

    create_response = client.post(
        "/groups/7/roles",
        data={"role_name": "Ny rolle", "pingvin_points": "5"},
        follow_redirects=False,
    )
    update_response = client.post(
        "/groups/7/roles/3?_method=PATCH",
        data={"role_name": "Oppdatert rolle", "pingvin_points": "8"},
        follow_redirects=False,
    )
    delete_response = client.post(
        "/groups/7/roles/3?_method=DELETE",
        follow_redirects=False,
    )

    assert create_response.status_code == 303
    assert create_response.headers["location"] == "/groups/7"
    assert groups_service.created_role == (7, "Ny rolle", 5)

    assert update_response.status_code == 303
    assert update_response.headers["location"] == "/groups/7"
    assert groups_service.updated_role == (7, 3, "Oppdatert rolle", 8)

    assert delete_response.status_code == 303
    assert delete_response.headers["location"] == "/groups/7"
    assert groups_service.deleted_role == (7, 3)


def test_group_archive_redirects_and_calls_service() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    groups_service = FakeGroupsService()
    app.dependency_overrides[get_groups_service] = lambda: groups_service
    client = TestClient(app)

    response = client.post(
        "/groups/7/archive",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/groups/7"
    assert groups_service.archived_group == 7


def test_group_role_assignment_create_redirects_and_calls_service() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    volunteers_service = FakeVolunteersService()
    app.dependency_overrides[get_volunteers_service] = lambda: volunteers_service
    app.dependency_overrides[get_role_assignments_service] = lambda: volunteers_service
    client = TestClient(app)

    response = client.post(
        "/groups/7/role-assignments",
        data={
            "volunteer_ids": ["12"],
            "role_id": "3",
            "year": "2026",
            "term": "2",
            "contract_signed": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/groups/7"
    assert volunteers_service.created_assignment == (12, 7, 3, 2026, 2, True)


def test_group_role_assignment_requires_exactly_one_volunteer() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    volunteers_service = FakeVolunteersService()
    app.dependency_overrides[get_volunteers_service] = lambda: volunteers_service
    app.dependency_overrides[get_role_assignments_service] = lambda: volunteers_service
    client = TestClient(app)

    response = client.post(
        "/groups/7/role-assignments",
        data={
            "volunteer_ids": ["12", "13"],
            "role_id": "3",
            "year": "2026",
            "term": "2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/groups/7?assignment_error=Velg%20n%C3%B8yaktig%20%C3%A9n%20frivillig."
    assert volunteers_service.created_assignment is None


def test_group_role_assignment_missing_semester_redirects_with_inline_error() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    volunteers_service = FakeVolunteersService()
    app.dependency_overrides[get_volunteers_service] = lambda: volunteers_service
    app.dependency_overrides[get_role_assignments_service] = lambda: volunteers_service
    client = TestClient(app)

    response = client.post(
        "/groups/7/role-assignments",
        data={
            "volunteer_ids": ["12"],
            "role_id": "3",
            "year": "2026",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == "/groups/7?assignment_error=Velg%20frivillig%2C%20verv%20og%20semester%20f%C3%B8r%20du%20legger%20til%20i%20gruppen."
    )
    assert volunteers_service.created_assignment is None


def test_course_completion_volunteer_search_returns_matches() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    volunteers_service = FakeVolunteersService()
    app.dependency_overrides[get_volunteers_service] = lambda: volunteers_service
    app.dependency_overrides[get_role_assignments_service] = lambda: volunteers_service
    client = TestClient(app)

    response = client.get("/volunteers/search/options/typeahead?q=sample")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "volunteer_id": 12,
                "full_name": "Sample Person",
            }
        ]
    }
    assert volunteers_service.last_search_option_query == "sample"
    assert volunteers_service.last_search_option_limit == 12


def test_course_completion_volunteer_search_short_query_returns_empty_list() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    volunteers_service = FakeVolunteersService()
    app.dependency_overrides[get_volunteers_service] = lambda: volunteers_service
    app.dependency_overrides[get_role_assignments_service] = lambda: volunteers_service
    client = TestClient(app)

    response = client.get("/volunteers/search/options/typeahead?q=s")

    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert volunteers_service.last_search_option_query is None


def test_course_completion_actions_redirect_and_call_service() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    courses_service = FakeCoursesService()
    app.dependency_overrides[get_courses_service] = lambda: courses_service
    client = TestClient(app)

    create_response = client.post(
        "/courses/4/completions",
        data={"volunteer_ids": ["13", "14"], "year": "2026", "term": "1"},
        follow_redirects=False,
    )
    delete_response = client.post(
        "/courses/4/completions/11?_method=DELETE",
        follow_redirects=False,
    )

    assert create_response.status_code == 303
    assert create_response.headers["location"] == "/courses/4"
    assert courses_service.created_completion == (4, [13, 14], 2026, 1)

    assert delete_response.status_code == 303
    assert delete_response.headers["location"] == "/courses/4"
    assert courses_service.deleted_completion == (4, 11)


def test_group_admin_can_manage_their_group() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(UserRole.GROUP_ADMIN))
    groups_service = FakeGroupsService()
    app.dependency_overrides[get_groups_service] = lambda: groups_service
    app.dependency_overrides[get_volunteers_service] = lambda: FakeVolunteersService()
    app.dependency_overrides[get_role_assignments_service] = lambda: FakeVolunteersService()
    client = TestClient(app)

    group_detail_response = client.get("/groups/7")
    create_response = client.post(
        "/groups/7/roles",
        data={"role_name": "Ny rolle", "pingvin_points": "5"},
        follow_redirects=False,
    )

    assert group_detail_response.status_code == 200
    assert "Opprett verv" in group_detail_response.text
    assert "Legg til frivillig" in group_detail_response.text
    assert create_response.status_code == 303
    assert create_response.headers["location"] == "/groups/7"
    assert groups_service.created_role == (7, "Ny rolle", 5)


def test_group_admin_can_manage_any_group() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(UserRole.GROUP_ADMIN))
    groups_service = FakeGroupsService()
    app.dependency_overrides[get_groups_service] = lambda: groups_service
    client = TestClient(app)

    response = client.post(
        "/groups/8/roles",
        data={"role_name": "Ny rolle", "pingvin_points": "5"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/groups/8"
    assert groups_service.created_role == (8, "Ny rolle", 5)


def test_group_history_delete_redirects_and_calls_service() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    groups_service = FakeGroupsService()
    app.dependency_overrides[get_groups_service] = lambda: groups_service
    client = TestClient(app)

    response = client.post(
        "/groups/7/history/9?_method=DELETE",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/groups/7"
    assert groups_service.deleted_history == (7, 9)


def test_group_history_delete_returns_oob_panels_for_htmx() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    groups_service = FakeGroupsService()
    app.dependency_overrides[get_groups_service] = lambda: groups_service
    client = TestClient(app)

    response = client.delete(
        "/groups/7/history/9",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert 'id="group-recent-history-panel"' in response.text
    assert 'id="group-history-panel"' in response.text
    assert 'id="group-stats-panel"' in response.text
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert groups_service.deleted_history == (7, 9)


def test_group_role_delete_returns_error_when_role_has_members() -> None:
    class BlockedGroupsService(FakeGroupsService):
        async def delete_group_role(self, group_id: int, role_id: int) -> bool:
            raise GroupRoleDeleteBlockedError(["Vervet har medlemmer og kan ikke slettes."])

    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    app.dependency_overrides[get_groups_service] = lambda: BlockedGroupsService()
    client = TestClient(app)

    delete_response = client.post(
        "/groups/7/roles/3?_method=DELETE",
        follow_redirects=False,
    )

    assert delete_response.status_code == 400
    assert delete_response.json() == {"detail": "Vervet har medlemmer og kan ikke slettes."}


@pytest.mark.parametrize("method,path", [("post", "/groups/7/roles"), ("patch", "/groups/7/roles/14")])
@pytest.mark.parametrize("points", [str(2**31), str(-(2**31)-1)])
def test_out_of_range_role_points_do_not_reach_service(method, path, points):
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    service = FakeGroupsService()
    app.dependency_overrides[get_groups_service] = lambda: service
    response = getattr(TestClient(app), method)(path, data={"role_name": "Role", "pingvin_points": points})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "pingvin_points"]
    assert service.created_role is None
    assert service.updated_role is None


def test_group_history_semester_members_load_on_demand() -> None:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    app.dependency_overrides[get_groups_service] = lambda: FakeGroupsService()
    client = TestClient(app)

    response = client.get("/groups/7/history/semesters/20261")

    assert response.status_code == 200
    assert 'href="/volunteers/12"' in response.text
    assert 'hx-confirm="Slette denne historikkraden?"' in response.text
    assert "<section" not in response.text
