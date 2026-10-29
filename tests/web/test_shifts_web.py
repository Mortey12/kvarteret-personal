from __future__ import annotations

from datetime import UTC, date, datetime, time

from fastapi.testclient import TestClient

from app.auth.roles import UserRole
from app.dependencies import get_shifts_service, get_shifts_setup_service
from app.domain.shifts.errors import ShiftForbiddenError, ShiftPlacementError
from app.domain.shifts.permissions import ALL_GROUPS, NO_GROUPS, GroupScope
from app.domain.shifts.models import (
    AssignedPerson,
    PlacementResult,
    PoolPerson,
    MemberRole,
    ShiftGroupOption,
    ShiftRecord,
    ShiftWishes,
    SlotRecord,
    ClosedPeriod,
    ClosedPeriodSection,
    ClosedShift,
    GenerationPreview,
    OtherGroup,
    PlannedShift,
    ShiftTemplate,
    TemplateSlot,
    WeekBanner,
    WeekPlan,
    WeekStatusView,
    GroupSettingsView,
)
from app.domain.shifts.planning import format_week, group_into_days, monday_of
from app.domain.shifts.service import ShiftsService
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
        self.can_open = True
        self.wishes: ShiftWishes | None = None

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
        days = group_into_days([record])
        if self.wishes is not None:
            days[0].venues[0].shifts[0].wishes = self.wishes
        week_start = monday_of(week)
        return WeekPlan(
            week_start=week_start,
            week_label=format_week(week_start),
            previous_week=date(2026, 10, 26),
            next_week=date(2026, 11, 9),
            selected_group_id=group_id,
            groups=[ShiftGroupOption(7, "Stjerne")],
            days=days,
            open_places=0,
            swap_requests=1,
            statuses=[WeekStatusView(7, "Stjerne", "open", "Åpen for ønsker", False, False, self.can_open)],
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
        self, group_id: int, *, app_enabled, samf_enabled, meeting_point, courses_enabled, scope: GroupScope
    ) -> str:
        self._check(scope)
        self.calls.append(("settings", group_id, app_enabled, samf_enabled, meeting_point, courses_enabled))
        return "Innstillingene for Stjerne er lagret."

    async def set_week_status(
        self, *, group_id: int, week: date, status: str, whole_month: bool, scope: GroupScope
    ) -> str:
        self._check(scope)
        self.calls.append(("week_status", group_id, week, status, whole_month))
        if status == "nonsense":
            raise ShiftPlacementError("Ugyldig status.")
        return "Uke 45 for Stjerne: publisert."

    async def remove_shift(self, *, shift_id: int, scope: GroupScope) -> str:
        self._check(scope)
        self.calls.append(("remove_shift", shift_id))
        if shift_id == 99:
            raise ShiftPlacementError("Det står folk på skiftet. Ta dem av først, eller steng skiftet i stedet.")
        return "Skiftet er fjernet."

    async def set_shift_closed(self, *, shift_id: int, closed: bool, scope: GroupScope) -> str:
        self._check(scope)
        self.calls.append(("set_shift_closed", shift_id, closed))
        return "Skiftet er stengt." if closed else "Skiftet er åpnet igjen."

    async def remove(self, *, assignment_id: int, scope: GroupScope) -> PlacementResult:
        self._check(scope)
        self.calls.append(("remove", assignment_id))
        return PlacementResult("Tatt av.")


class FakeSetupService:
    def __init__(self, shifts: FakeShiftsService) -> None:
        self.shifts = shifts
        self.calls: list[tuple] = []

    def _check(self, scope: GroupScope) -> None:
        assert scope == self.shifts.scope
        if self.shifts.forbidden:
            raise ShiftForbiddenError("Du har ikke tilgang til å endre skift for Grøndahls.")

    async def own_groups(self, scope):
        return [ShiftGroupOption(7, "Stjerne")]

    async def list_closed_periods(self, scope):
        return [
            ClosedPeriodSection(
                7,
                "Stjerne",
                own=[ClosedPeriod(4, 7, date(2026, 12, 19), date(2027, 1, 5), "Juleferie", "Stjerne")],
                inherited=[ClosedPeriod(5, 1, date(2027, 4, 1), date(2027, 4, 5), "Påske", "Skjenkegruppen")],
            )
        ]

    async def list_other_groups(self, scope):
        return [OtherGroup(20, "Vakt")] if scope.is_all else []

    async def add_group(self, group_id, *, scope):
        self._check(scope)
        self.calls.append(("add_group", group_id))
        return "Vakt tar nå del i Skiftlista."

    async def add_closed_period(self, group_id, *, starts_on, ends_on, label, scope):
        self._check(scope)
        self.calls.append(("add_period", group_id, starts_on, ends_on, label))
        if ends_on < starts_on:
            raise ShiftPlacementError("Sluttdatoen kan ikke være før startdatoen.")
        return "Juleferie er lagt til."

    async def remove_closed_period(self, period_id, *, scope):
        self._check(scope)
        self.calls.append(("remove_period", period_id))
        return "Juleferie er fjernet."

    async def list_templates(self, scope):
        return [
            ShiftTemplate(
                3, 7, "Stjerne", "Stjernesalen", "Dagskift", 0, time(11, 30), time(17), True, True,
                [TemplateSlot("skiftleder", 1, False, 0), TemplateSlot("skjenker", 2, True, 1)],
            )
        ]

    async def create_template(self, group_id, form, *, scope):
        self._check(scope)
        self.calls.append(("create_template", group_id, dict(form)))
        if not form.get("title"):
            raise ShiftPlacementError("Tittel må fylles ut.")
        return "Malen er lagt til."

    async def update_template(self, template_id, form, *, scope):
        self._check(scope)
        self.calls.append(("update_template", template_id, dict(form)))
        return "Malen er lagret."

    async def set_template_active(self, template_id, active, *, scope):
        self._check(scope)
        self.calls.append(("template_active", template_id, active))
        return "Malen er slått av."

    def _preview(self, group_id, date_from, date_to) -> GenerationPreview:
        start = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)
        planned = PlannedShift(3, group_id, "Stjernesalen", "Dagskift", date(2026, 11, 2), start, start, True, [])
        return GenerationPreview(group_id, "Stjerne", date_from, date_to, [planned], [planned], [ClosedShift(planned, ClosedPeriod(5, 1, date(2026, 12, 19), date(2027, 1, 5), "Juleferie", "Skjenkegruppen"), True)])

    async def preview_generation(self, group_id, date_from, date_to, *, scope):
        self._check(scope)
        return self._preview(group_id, date_from, date_to)

    async def generate(self, group_id, date_from, date_to, *, scope):
        self._check(scope)
        self.calls.append(("generate", group_id, date_from, date_to))
        return self._preview(group_id, date_from, date_to), "Opprettet 1 skift for Stjerne."


def _client(role: UserRole) -> tuple[TestClient, FakeShiftsService]:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(role))
    service = FakeShiftsService()
    app.dependency_overrides[get_shifts_service] = lambda: service
    setup = FakeSetupService(service)
    service.setup = setup
    app.dependency_overrides[get_shifts_setup_service] = lambda: setup
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


def test_settings_page_warns_about_missing_courses_only_when_present() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    assert "data-missing-courses" not in client.get("/shifts/settings").text

    async def missing(scope):
        return [GroupSettingsView(7, "Stjerne", True, True, False, "", True, ["Brannkurs", "Barkurs"])]

    service.list_group_settings = missing
    text = client.get("/shifts/settings").text
    assert "data-missing-courses" in text
    assert "Fant ikke kurset Brannkurs og Barkurs" in text


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
        data={"app_enabled": "true", "courses_enabled": "true", "meeting_point": "Ved baren"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )
    off = client.post("/shifts/settings/7", data={"meeting_point": ""}, headers=csrf_headers(client), follow_redirects=False)

    assert saved.status_code == 303
    assert saved.headers["location"].startswith("/shifts/settings?notice=")
    assert ("settings", 7, True, False, "Ved baren", True) in service.calls
    assert ("settings", 7, False, False, "", False) in service.calls
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

    for label in ("Åpne for ønsker", "Publiser uka", "Publiser måneden"):
        assert label in page.text
    assert "Fordeles nå" not in page.text
    response = client.post(
        "/shifts/weeks/status",
        data={"group_id": "7", "week": "2026-11-02", "status": "open", "view_group": "7"},
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
    assert ("week_status", 7, date(2026, 11, 2), "open", False) in service.calls
    assert ("week_status", 7, date(2026, 11, 2), "published", True) in service.calls
    assert month.status_code == 303


def test_open_for_wishes_button_is_disabled_for_the_current_week() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    enabled = client.get("/shifts?week=2026-11-04").text
    service.can_open = False
    disabled = client.get("/shifts?week=2026-11-04").text

    def open_button(page: str) -> str:
        form = page.split('name="status" value="open"')[1]
        return form.split("</form>")[0]

    assert "disabled" not in open_button(enabled)
    assert "disabled" in open_button(disabled)


def test_board_has_close_and_delete_buttons_with_confirm_for_empty_shifts() -> None:
    client, _ = _client(UserRole.GROUP_ADMIN)
    page = client.get("/shifts?week=2026-11-02").text
    assert 'action="/shifts/shifts/1/closed"' in page and "Steng skift" in page
    # The fake shift has people on it, so it can be closed but not removed.
    assert 'action="/shifts/shifts/1/delete"' not in page


def test_shift_close_open_and_delete_post_scoped_csrf_checked_actions() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    prime_csrf(client, path="/shifts")

    closed = client.post(
        "/shifts/shifts/1/closed",
        data={"closed": "true", "week": "2026-11-02", "view_group": "7"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )
    reopened = client.post(
        "/shifts/shifts/1/closed", data={"closed": "false", "week": "2026-11-02"}, headers=csrf_headers(client), follow_redirects=False
    )
    removed = client.post(
        "/shifts/shifts/2/delete", data={"week": "2026-11-02"}, headers=csrf_headers(client), follow_redirects=False
    )
    refused = client.post(
        "/shifts/shifts/99/delete", data={"week": "2026-11-02"}, headers=csrf_headers(client), follow_redirects=False
    )

    assert closed.status_code == 303
    assert closed.headers["location"].startswith("/shifts?week=2026-11-02&group=7&notice=")
    assert ("set_shift_closed", 1, True) in service.calls and ("set_shift_closed", 1, False) in service.calls
    assert reopened.status_code == 303 and removed.status_code == 303
    assert ("remove_shift", 2) in service.calls
    # A shift with people is refused with the reason shown on the board.
    assert refused.status_code == 303 and "error=" in refused.headers["location"]


def test_shift_actions_need_csrf_and_respect_scope() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    client.cookies.set(client.app.state.container.settings.session_cookie_name, "signed-in")
    no_token = client.post("/shifts/shifts/1/delete", data={}, follow_redirects=False)
    assert no_token.status_code == 403
    assert not any(call[0] == "remove_shift" for call in service.calls if isinstance(call[0], str))
    prime_csrf(client, path="/shifts")
    service.forbidden = True
    forbidden = client.post("/shifts/shifts/1/closed", data={"closed": "true"}, headers=csrf_headers(client))
    assert forbidden.status_code == 403 and "Du har ikke tilgang" in forbidden.json()["detail"]


class _PoolRepository:
    """Just enough of ShiftsRepository for ShiftsService.assign, with a real service on top."""

    def __init__(self) -> None:
        start = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)
        self.shift = ShiftRecord(
            shift_id=1, group_id=7, group_name="Stjerne", venue="Stjernesalen", title="Dagskift",
            starts_at=start, ends_at=start.replace(hour=16), note=None, is_closed=False,
            training_replaces_slot=True,
            slots=[SlotRecord(11, "skjenker", 2, False, 0, [])],
        )
        self.names = {3: "Sander Skjenker", 77: "Hemmelig Utenfor"}
        self.added: list[tuple[int, int]] = []

    async def list_group_parents(self):
        return {7: None}

    async def list_direct_participating_group_ids(self):
        return [7]

    async def list_shift_members(self, semester, group_ids):
        return [MemberRole(3, "Sander Skjenker", 7, "Stjerne", "Skjenker")]

    async def get_slot_shift_id(self, slot_id):
        return 1 if slot_id == 11 else None

    async def get_shift(self, shift_id):
        return self.shift

    async def lock_shift(self, shift_id):
        return None

    async def get_volunteer_name(self, volunteer_id):
        return self.names.get(volunteer_id)

    async def list_shift_groups(self):
        return [ShiftGroupOption(7, "Stjerne")]

    async def get_volunteer_birth_date(self, volunteer_id):
        return date(2000, 1, 1)

    async def count_shifts_by_volunteer(self, starts_from, starts_before):
        return {}

    async def add_assignment(self, slot_id, volunteer_id):
        self.added.append((slot_id, volunteer_id))
        return 1


def _pool_client() -> tuple[TestClient, _PoolRepository]:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(UserRole.ADMIN))
    repository = _PoolRepository()
    app.dependency_overrides[get_shifts_service] = lambda: ShiftsService(repository)
    return TestClient(app), repository


def test_assign_refuses_a_volunteer_outside_the_pool_with_a_neutral_message() -> None:
    client, repository = _pool_client()
    prime_csrf(client, path="/")

    outside = client.post(
        "/shifts/assignments", data={"slot_id": "11", "volunteer_id": "77"}, headers=csrf_headers(client)
    )
    missing = client.post(
        "/shifts/assignments", data={"slot_id": "11", "volunteer_id": "4040"}, headers=csrf_headers(client)
    )

    assert outside.status_code == missing.status_code == 400
    assert outside.json() == missing.json() == {"detail": "Fann ikke personen i gruppa."}
    assert "Hemmelig" not in outside.text
    assert repository.added == []


def test_assign_still_places_a_pool_member() -> None:
    client, repository = _pool_client()
    prime_csrf(client, path="/")

    ok = client.post(
        "/shifts/assignments", data={"slot_id": "11", "volunteer_id": "3"}, headers=csrf_headers(client)
    )

    assert ok.status_code == 200
    assert ok.json()["message"].startswith("Sander Skjenker er satt opp som")
    assert repository.added == [(11, 3)]


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


def test_settings_page_lists_closed_periods_and_other_groups_for_admin_only() -> None:
    admin, admin_service = _client(UserRole.ADMIN)
    page = admin.get("/shifts/settings").text
    assert "Juleferie" in page and "19.12.2026–05.01.2027" in page
    assert "Påske" in page and "Fra Skjenkegruppen" in page
    assert page.count("data-inherited-period") == 1
    assert "Andre grupper" in page and "Slå på Skiftlista for gruppen" in page
    assert 'action="/shifts/settings/20/enable"' in page

    leader, leader_service = _client(UserRole.GROUP_ADMIN)
    leader_service.scope = GroupScope(frozenset({7}))
    leader_page = leader.get("/shifts/settings").text
    assert "Juleferie" in leader_page and "Andre grupper" not in leader_page


def test_closed_period_add_and_remove_are_scoped_and_validated() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    prime_csrf(client, path="/shifts")
    ok = client.post(
        "/shifts/settings/7/closed-periods",
        data={"starts_on": "2026-12-19", "ends_on": "2027-01-05", "label": "Juleferie"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )
    backwards = client.post(
        "/shifts/settings/7/closed-periods",
        data={"starts_on": "2027-01-05", "ends_on": "2026-12-19", "label": ""},
        headers=csrf_headers(client),
        follow_redirects=False,
    )
    removed = client.post("/shifts/closed-periods/4/delete", headers=csrf_headers(client), follow_redirects=False)

    assert ok.status_code == 303 and "notice=" in ok.headers["location"]
    assert ("add_period", 7, date(2026, 12, 19), date(2027, 1, 5), "Juleferie") in service.setup.calls
    assert backwards.status_code == 303 and "error=" in backwards.headers["location"]
    assert removed.status_code == 303 and ("remove_period", 4) in service.setup.calls

    service.forbidden = True
    refused = client.post(
        "/shifts/settings/8/closed-periods",
        data={"starts_on": "2026-12-19", "ends_on": "2027-01-05"},
        headers=csrf_headers(client),
    )
    assert refused.status_code == 403
    assert client.post("/shifts/closed-periods/9/delete", headers=csrf_headers(client)).status_code == 403


def test_only_admin_flow_adds_a_group() -> None:
    client, service = _client(UserRole.ADMIN)
    prime_csrf(client, path="/shifts")
    response = client.post("/shifts/settings/20/enable", headers=csrf_headers(client), follow_redirects=False)
    assert response.status_code == 303 and ("add_group", 20) in service.setup.calls
    service.forbidden = True
    assert client.post("/shifts/settings/20/enable", headers=csrf_headers(client)).status_code == 403


def test_templates_page_lists_templates_with_their_places() -> None:
    client, _ = _client(UserRole.GROUP_ADMIN)
    page = client.get("/shifts/templates").text
    assert "Dagskift" in page and "11:30–17:00" in page and "1 skiftleder, 2 skjenker" in page
    assert 'action="/shifts/templates/3"' in page and 'action="/shifts/templates"' in page
    assert "Slutt før start betyr at skiftet slutter etter midnatt." in page
    assert "Slå av malen" in page
    assert client.get("/shifts").status_code == 200 and "/shifts/templates" in client.get("/shifts").text


def test_template_create_update_and_deactivate() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    prime_csrf(client, path="/shifts")
    created = client.post(
        "/shifts/templates",
        data={"group_id": "7", "title": "Kveldsskift", "venue": "Stjernesalen", "slot_kind_0": "skjenker"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )
    invalid = client.post(
        "/shifts/templates", data={"group_id": "7", "title": ""}, headers=csrf_headers(client), follow_redirects=False
    )
    updated = client.post(
        "/shifts/templates/3", data={"title": "Dagskift"}, headers=csrf_headers(client), follow_redirects=False
    )
    off = client.post(
        "/shifts/templates/3/active", data={"active": "false"}, headers=csrf_headers(client), follow_redirects=False
    )

    assert created.status_code == 303 and "notice=" in created.headers["location"]
    assert service.setup.calls[0][:2] == ("create_template", 7)
    assert service.setup.calls[0][2]["slot_kind_0"] == "skjenker"
    assert invalid.status_code == 303 and "error=" in invalid.headers["location"]
    assert updated.status_code == 303 and ("template_active", 3, False) in service.setup.calls
    assert off.status_code == 303

    service.forbidden = True
    assert client.post("/shifts/templates/3", data={"title": "x"}, headers=csrf_headers(client)).status_code == 403


def test_generate_page_shows_the_preview_counts_and_confirm_creates() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    page = client.get("/shifts/generate?group=7&date_from=2026-11-01&date_to=2026-12-31").text
    assert 'data-count="create"><strong>1</strong> skift blir laget' in page
    assert 'data-count="existing"><strong>1</strong> finnes fra før' in page
    assert 'data-count="closed"><strong>1</strong> hoppes over fordi gruppen er stengt' in page
    assert "Juleferie" in page and "fra Skjenkegruppen" in page and "Lag 1 skift" in page

    prime_csrf(client, path="/shifts")
    confirmed = client.post(
        "/shifts/generate",
        data={"group_id": "7", "date_from": "2026-11-01", "date_to": "2026-12-31"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    assert confirmed.headers["location"].startswith("/shifts/generate?")
    assert "group=7" in confirmed.headers["location"] and "notice=" in confirmed.headers["location"]
    assert ("generate", 7, date(2026, 11, 1), date(2026, 12, 31)) in service.setup.calls

    service.forbidden = True
    assert (
        client.post(
            "/shifts/generate",
            data={"group_id": "8", "date_from": "2026-11-01", "date_to": "2026-12-31"},
            headers=csrf_headers(client),
        ).status_code
        == 403
    )


def test_board_renders_wishes_as_a_collapsed_details_with_names() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    assert "data-shift-wishes" not in client.get("/shifts?week=2026-11-02").text

    service.wishes = ShiftWishes(
        vil=["Vera Vil", "Ola Vil"], kan=["Kari Kan"], nei=["Nora Nei"], ikke_satt=["Ida Ikke", "Per Ikke"]
    )
    page = client.get("/shifts?week=2026-11-02").text
    assert "Ønsker: 2 vil · 1 kan · 1 nei · 2 ikke satt" in page
    details = page.split("data-shift-wishes")[1].split("</details>")[0]
    assert "<summary" in details and "<details" in page and " open" not in page.split("data-shift-wishes")[0][-60:]
    for name in ("Vera Vil", "Ola Vil", "Kari Kan", "Nora Nei", "Ida Ikke", "Per Ikke"):
        assert name in details
