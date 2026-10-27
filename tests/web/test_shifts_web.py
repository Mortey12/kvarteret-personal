from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.auth.roles import UserRole
from app.dependencies import get_shifts_service
from app.domain.shifts.errors import ShiftForbiddenError, ShiftPlacementError
from app.domain.shifts.permissions import ALL_GROUPS, NO_GROUPS, GroupScope
from app.domain.shifts.models import (
    AssignedPerson,
    PlacementResult,
    PoolPerson,
    ShiftGroupOption,
    ShiftRecord,
    SlotRecord,
    WeekBanner,
    WeekPlan,
    WeekStatusView,
    GroupSettingsView,
)
from app.domain.shifts.planning import format_week, group_into_days, monday_of
from app.main import create_app
from tests.support.helpers import (
    csrf_headers,
    make_authenticated_user,
    override_authenticated_user,
    prime_csrf,
)


class FakeShiftsService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.scope = ALL_GROUPS
        self.forbidden = False
        self.banners: list[WeekBanner] = []

    async def manageable_scope(self, role, auth_user_id) -> GroupScope:
        self.calls.append(("scope", role))
        return self.scope

    def _check(self, scope: GroupScope) -> None:
        assert scope == self.scope
        if self.forbidden:
            raise ShiftForbiddenError("Du har ikke tilgang til å endre skift for Grøndahls.")

    async def get_week_plan(
        self, week: date, group_id: int | None = None, *, scope: GroupScope
    ) -> WeekPlan:
        self._check(scope)
        self.calls.append((week, group_id))
        start = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)
        record = ShiftRecord(
            shift_id=1,
            group_id=7,
            group_name="Stjerne",
            venue="Stjernesalen",
            title="Dagskift",
            starts_at=start,
            ends_at=start.replace(hour=16),
            note="Reservert bord 22 fra 18",
            is_closed=False,
            training_replaces_slot=True,
            slots=[
                SlotRecord(
                    1,
                    "skiftleder",
                    1,
                    False,
                    0,
                    [
                        AssignedPerson(
                            1,
                            1,
                            "Birk Lie",
                            swap_requested=True,
                            warnings=["har allerede 1 skift denne uka"],
                        )
                    ],
                ),
                SlotRecord(
                    2,
                    "skjenker",
                    1,
                    False,
                    1,
                    [AssignedPerson(3, 4, "Ulrik Unavailable", swap_requested=False, cannot_attend=True)],
                ),
                SlotRecord(
                    3,
                    "opplaering",
                    1,
                    False,
                    2,
                    [AssignedPerson(2, 2, "Vilde Moen", swap_requested=False)],
                ),
            ],
        )
        week_start = monday_of(week)
        return WeekPlan(
            week_start=week_start,
            week_label=format_week(week_start),
            previous_week=date(2026, 10, 26),
            next_week=date(2026, 11, 9),
            selected_group_id=group_id,
            groups=[ShiftGroupOption(7, "Stjerne")],
            days=group_into_days([record]),
            open_places=0,
            swap_requests=1,
            statuses=[WeekStatusView(7, "Stjerne", "open", "Åpen for ønsker", False, False)],
            banners=self.banners,
            pool=[
                PoolPerson(
                    volunteer_id=3,
                    name="Sander Skjenker",
                    group_name="Stjerne",
                    role_name="Skjenker",
                    shifts_this_week=0,
                    allowed_tokens=["7:skjenker:1"],
                )
            ],
        )

    async def assign(self, *, slot_id: int, volunteer_id: int, scope: GroupScope) -> PlacementResult:
        self._check(scope)
        self.calls.append(("assign", slot_id, volunteer_id))
        if slot_id == 1:
            raise ShiftPlacementError("Kun for skiftledere i gruppen.")
        return PlacementResult("Sander Skjenker er satt opp som skjenker.")

    async def move(self, *, assignment_id: int, slot_id: int, scope: GroupScope) -> PlacementResult:
        self._check(scope)
        self.calls.append(("move", assignment_id, slot_id))
        return PlacementResult("Flyttet.")

    async def list_group_settings(self, scope: GroupScope) -> list[GroupSettingsView]:
        self._check(scope)
        return [GroupSettingsView(7, "Stjerne", True, True, False, "Ved baren")]

    async def save_group_settings(
        self, group_id: int, *, app_enabled, samf_enabled, meeting_point, scope: GroupScope
    ) -> str:
        self._check(scope)
        self.calls.append(("settings", group_id, app_enabled, samf_enabled, meeting_point))
        return "Innstillingene for Stjerne er lagret."

    async def set_week_status(
        self, *, group_id: int, week: date, status: str, whole_month: bool, scope: GroupScope
    ) -> str:
        self._check(scope)
        self.calls.append(("week_status", group_id, week, status, whole_month))
        if status == "nonsense":
            raise ShiftPlacementError("Ugyldig status.")
        return "Uke 45 for Stjerne: publisert."

    async def remove(self, *, assignment_id: int, scope: GroupScope) -> PlacementResult:
        self._check(scope)
        self.calls.append(("remove", assignment_id))
        return PlacementResult("Tatt av.")


def _client(role: UserRole) -> tuple[TestClient, FakeShiftsService]:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(role))
    service = FakeShiftsService()
    app.dependency_overrides[get_shifts_service] = lambda: service
    return TestClient(app), service


def test_shift_plan_renders_week_for_managers() -> None:
    client, service = _client(UserRole.ADMIN)
    response = client.get("/shifts?week=2026-11-04&group=7")

    assert response.status_code == 200
    assert (date(2026, 11, 4), 7) in service.calls
    assert "Skiftplan" in response.text
    assert "Uke 45 · 2.–8. november" in response.text
    assert "Mandag 2. november" in response.text
    assert "Stjernesalen og kjøkken" in response.text
    assert "Birk Lie" in response.text
    assert "ønsker bytte" in response.text
    assert "Har allerede 1 skift denne uka" in response.text
    # "Kan ikke i dag": still listed, the place counts as open and opens to everyone.
    assert "Ulrik Unavailable" in response.text and "kan ikke i dag" in response.text
    assert "Åpen for alle (kan ikke i dag)" in response.text
    assert "Brukt til opplæring" in response.text
    assert "Reservert bord 22 fra 18" in response.text
    assert 'href="/shifts?week=2026-11-09&amp;group=7"' in response.text


def test_shift_plan_ignores_bad_query_values() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    response = client.get("/shifts?week=not-a-date&group=abc")

    assert response.status_code == 200
    assert [c for c in service.calls if c[0] != "scope"][0][1] is None


def test_shift_plan_is_for_managers_only() -> None:
    client, _ = _client(UserRole.VOLUNTEER)
    response = client.get("/shifts")

    assert response.status_code == 403


def test_shift_plan_shows_volunteer_pool_and_drop_targets() -> None:
    client, _ = _client(UserRole.ADMIN)
    response = client.get("/shifts")

    assert "Sander Skjenker" in response.text
    assert 'data-allowed="7:skjenker:1"' in response.text
    assert 'data-shift-slot data-slot-id="2" data-group-id="7" data-kind="skjenker" data-open="1"' in response.text
    assert 'data-remove-assignment="1"' in response.text
    assert "js/shift-board.js" in response.text


def test_shift_actions_save_and_report_rules() -> None:
    client, service = _client(UserRole.ADMIN)
    prime_csrf(client, path="/shifts")

    ok = client.post(
        "/shifts/assignments", data={"slot_id": "2", "volunteer_id": "3"}, headers=csrf_headers(client)
    )
    refused = client.post(
        "/shifts/assignments", data={"slot_id": "1", "volunteer_id": "3"}, headers=csrf_headers(client)
    )
    moved = client.post("/shifts/assignments/1/move", data={"slot_id": "2"}, headers=csrf_headers(client))
    removed = client.post("/shifts/assignments/1/delete", headers=csrf_headers(client))

    assert ok.status_code == 200
    assert ok.json() == {"message": "Sander Skjenker er satt opp som skjenker."}
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Kun for skiftledere i gruppen."
    assert moved.json() == {"message": "Flyttet."}
    assert removed.json() == {"message": "Tatt av."}
    assert ("move", 1, 2) in service.calls and ("remove", 1) in service.calls


def test_shift_actions_need_csrf_and_management_role() -> None:
    client, service = _client(UserRole.ADMIN)
    # CSRF applies to signed-in browsers, recognised by the session cookie.
    client.cookies.set(client.app.state.container.settings.session_cookie_name, "signed-in")
    assert client.post("/shifts/assignments", data={"slot_id": "2", "volunteer_id": "3"}).status_code == 403

    volunteer_client, _ = _client(UserRole.VOLUNTEER)
    prime_csrf(volunteer_client, path="/")
    response = volunteer_client.post(
        "/shifts/assignments/1/delete", headers=csrf_headers(volunteer_client)
    )
    assert response.status_code == 403
    assert not any(call[0] == "remove" for call in service.calls if isinstance(call[0], str))



def test_gruppeadmin_scope_is_passed_to_the_service() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    service.scope = GroupScope(frozenset({7}))
    assert client.get("/shifts").status_code == 200
    assert ("scope", UserRole.GROUP_ADMIN) in service.calls


def test_actions_on_a_forbidden_group_return_403_with_a_message() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    prime_csrf(client, path="/shifts")
    service.forbidden = True

    responses = [
        client.post(
            "/shifts/assignments", data={"slot_id": "2", "volunteer_id": "3"}, headers=csrf_headers(client)
        ),
        client.post("/shifts/assignments/1/move", data={"slot_id": "2"}, headers=csrf_headers(client)),
        client.post("/shifts/assignments/1/delete", headers=csrf_headers(client)),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["detail"] == "Du har ikke tilgang til å endre skift for Grøndahls."


def test_gruppeadmin_without_groups_sees_an_empty_state() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    service.scope = NO_GROUPS
    empty = FakeShiftsService.get_week_plan

    async def no_plan(self, week, group_id=None, *, scope):
        plan = await empty(self, week, group_id, scope=scope)
        plan.groups, plan.days, plan.pool = [], [], []
        return plan

    FakeShiftsService.get_week_plan = no_plan
    try:
        response = client.get("/shifts")
    finally:
        FakeShiftsService.get_week_plan = empty

    assert response.status_code == 200
    assert "Du er ikke gruppeadmin for noen grupper med skift" in response.text


def test_settings_page_shows_current_state_and_the_app_note() -> None:
    client, _ = _client(UserRole.GROUP_ADMIN)
    response = client.get("/shifts/settings")

    assert response.status_code == 200
    assert "Stjerne" in response.text and "På i appen" in response.text
    assert 'value="Ved baren"' in response.text
    assert 'action="/shifts/settings/7"' in response.text
    assert "blir skiftene synlige for medlemmene i den gruppa i appen" in response.text


def test_settings_page_is_for_managers_only() -> None:
    client, _ = _client(UserRole.VOLUNTEER)
    assert client.get("/shifts/settings").status_code == 403


def test_settings_page_without_groups_shows_the_empty_state() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    service.scope = NO_GROUPS

    async def nothing(scope):
        return []

    service.list_group_settings = nothing
    response = client.get("/shifts/settings")
    assert response.status_code == 200
    assert "Du er ikke gruppeadmin for noen grupper med skift" in response.text


def test_settings_are_saved_with_switches_and_meeting_point() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    prime_csrf(client, path="/shifts")

    saved = client.post(
        "/shifts/settings/7",
        data={"app_enabled": "true", "meeting_point": "Ved baren"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )
    off = client.post("/shifts/settings/7", data={"meeting_point": ""}, headers=csrf_headers(client), follow_redirects=False)

    assert saved.status_code == 303
    assert saved.headers["location"].startswith("/shifts/settings?notice=")
    assert ("settings", 7, True, False, "Ved baren") in service.calls
    assert ("settings", 7, False, False, "") in service.calls
    assert off.status_code == 303


def test_settings_for_another_group_are_refused() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    prime_csrf(client, path="/shifts")
    service.forbidden = True

    response = client.post("/shifts/settings/8", data={"app_enabled": "true"}, headers=csrf_headers(client))

    assert response.status_code == 403
    assert "Du har ikke tilgang" in response.json()["detail"]
    assert not any(call[0] == "settings" for call in service.calls if isinstance(call[0], str))


def test_week_status_buttons_post_explicit_status() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    page = client.get("/shifts?week=2026-11-04")
    prime_csrf(client, path="/shifts")

    for label in ("Åpne for ønsker", "Fordeles nå", "Publiser uka", "Publiser måneden"):
        assert label in page.text
    response = client.post(
        "/shifts/weeks/status",
        data={"group_id": "7", "week": "2026-11-02", "status": "allocating", "view_group": "7"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )
    month = client.post(
        "/shifts/weeks/status",
        data={"group_id": "7", "week": "2026-11-02", "status": "published", "whole_month": "true"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/shifts?week=2026-11-02&group=7&notice=")
    assert ("week_status", 7, date(2026, 11, 2), "allocating", False) in service.calls
    assert ("week_status", 7, date(2026, 11, 2), "published", True) in service.calls
    assert month.status_code == 303


def test_week_status_for_another_group_is_refused_and_bad_status_reports_an_error() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    prime_csrf(client, path="/shifts")

    bad = client.post(
        "/shifts/weeks/status",
        data={"group_id": "7", "week": "2026-11-02", "status": "nonsense"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )
    assert bad.status_code == 303 and "error=" in bad.headers["location"]

    service.forbidden = True
    refused = client.post(
        "/shifts/weeks/status",
        data={"group_id": "8", "week": "2026-11-02", "status": "published"},
        headers=csrf_headers(client),
    )
    assert refused.status_code == 403


def test_banner_is_shown_for_an_unpublished_week_and_hidden_otherwise() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    assert "data-week-banner" not in client.get("/shifts").text

    service.banners = [
        WeekBanner(7, "Stjerne", date(2026, 11, 30), "Uke 49 er ikke publisert. Den blir åpen for alle mandag 30. november.", False)
    ]
    calm = client.get("/shifts").text
    assert "data-week-banner" in calm
    assert "Uke 49 er ikke publisert. Den blir åpen for alle mandag 30. november." in calm
    assert "border-amber-500" not in calm

    service.banners[0].urgent = True
    urgent = client.get("/shifts").text
    assert "border-amber-500" in urgent and 'role="alert"' in urgent
    assert '<input type="hidden" name="week" value="2026-11-30">' in urgent
