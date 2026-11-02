from __future__ import annotations

import json
import re

import pytest
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.auth.roles import UserRole
from app.dependencies import get_shifts_service
from app.domain.shifts.models import (
    AssignedPerson,
    PoolPerson,
    ShiftGroupOption,
    ShiftRecord,
    SlotRecord,
    WeekBanner,
    WeekPlan,
    WeekStatusView,
)
from app.domain.shifts.permissions import ALL_GROUPS, NO_GROUPS, GroupScope
from app.domain.shifts.planning import format_week, group_into_days, monday_of
from app.main import create_app
from tests.support.helpers import csrf_headers, make_authenticated_user, override_authenticated_user, prime_csrf


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def list_shift_groups(self):
        return [ShiftGroupOption(7, "Stjerne"), ShiftGroupOption(8, "Grøndahls")]

    async def list_wishes_for(self, shift_ids, volunteer_ids):
        self.calls.append(("wishes", tuple(shift_ids), tuple(volunteer_ids)))
        return {(1, 4): "vil", (1, 3): "nei", (2, 3): "kan"}

    async def list_shifts_between(self, starts_from, starts_before, group_id=None, *, group_ids=None):
        return [self.record(1, 2, 11), self.record(2, 3, 11)]

    async def count_shifts_by_volunteer(self, starts_from, starts_before):
        return {3: 2, 4: 1}

    async def list_answered_volunteers(self, group_ids, starts_from, starts_before):
        return {7: {4}}

    async def get_week_statuses(self, group_ids, weeks):
        return {(7, date(2026, 11, 9)): "published"}

    @staticmethod
    def record(shift_id: int, day: int, hour: int) -> ShiftRecord:
        start = datetime(2026, 11, day, hour - 1, 0, tzinfo=UTC)
        return ShiftRecord(
            shift_id=shift_id,
            group_id=7,
            group_name="Stjerne",
            venue="Stjernesalen",
            title="Dagskift",
            starts_at=start,
            ends_at=start.replace(hour=hour + 5),
            note=None,
            is_closed=False,
            training_replaces_slot=False,
            slots=[
                SlotRecord(shift_id * 10, "skiftleder", 1, False, 0, []),
                SlotRecord(
                    shift_id * 10 + 1,
                    "skjenker",
                    2,
                    False,
                    1,
                    [AssignedPerson(shift_id * 100, 3, "Sander Skjenker", False)],
                ),
            ],
        )


class FakeService:
    def __init__(self, scope: GroupScope = ALL_GROUPS) -> None:
        self.scope = scope
        self.repository = FakeRepository()
        self.plan_calls: list[tuple] = []
        self.banners: list[WeekBanner] = []
        self.can_open = True
        self.shift_days = [2, 3]

    async def manageable_scope(self, role, auth_user_id) -> GroupScope:
        return self.scope

    async def get_week_plan(self, week, group_id=None, *, scope):
        self.plan_calls.append((week, group_id))
        records = [FakeRepository.record(i + 1, day, 11) for i, day in enumerate(self.shift_days)]
        week_start = monday_of(week)
        return WeekPlan(
            week_start=week_start,
            week_label=format_week(week_start),
            previous_week=date(2026, 10, 26),
            next_week=date(2026, 11, 9),
            selected_group_id=group_id,
            groups=[ShiftGroupOption(7, "Stjerne")],
            days=group_into_days(records),
            open_places=4,
            swap_requests=0,
            statuses=[WeekStatusView(7, "Stjerne", "open", "Åpen for ønsker", True, False, self.can_open)],
            banners=self.banners,
            pool=[
                PoolPerson(3, "Sander Skjenker", "Stjerne", "Skjenker", 2, ["7:skjenker:0"]),
                PoolPerson(4, "Tora Tilgjengelig", "Stjerne", "Skjenker", 1, ["7:skjenker:0", "7:skiftleder:0"]),
            ],
        )


def _client(role: UserRole = UserRole.ADMIN, scope: GroupScope = ALL_GROUPS) -> tuple[TestClient, FakeService]:
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(role))
    service = FakeService(scope)
    app.dependency_overrides[get_shifts_service] = lambda: service
    return TestClient(app), service


def _data(html: str) -> dict:
    found = re.search(r'<script type="application/json" id="week-data">(.*?)</script>', html, re.S)
    assert found, "the page has no week-data block"
    return json.loads(found.group(1))


def test_week_page_renders_grid_people_chips_and_data_block() -> None:
    client, service = _client()
    response = client.get("/shifts/week?week=2026-11-04&group=7")

    assert response.status_code == 200
    html = response.text
    assert (date(2026, 11, 4), 7) in service.plan_calls
    # Week bar.
    assert "Uke 45 (2.–8. nov)" in html
    assert "Utkast: ikke synlig i appen" in html
    assert "Åpne for ønsker" in html and "Publiser uka" in html
    assert 'name="csrf_token"' in html
    # Chips: Sander answered kan ikke on Monday (red) and is on two shifts (yellow on the other day):
    # two places have problems, one of them red, so the chip says "1 problem" and lists both.
    assert re.search(r'data-chip="problems"[^>]*><span aria-hidden="true">! </span>1 problem</summary>', html)
    assert html.count("Sander Skjenker") >= 2
    assert re.search(r'data-chip="open"[^>]*>\d+ ledige', html)
    # Tora began answering and has no shift this week.
    assert re.search(r'data-chip="without-shift"[^>]*>1 uten skift', html)
    # Top line.
    assert "Har begynt 1 av 2" in html
    assert "Vis hvem" in html and "<li>Sander Skjenker</li>" in html
    # Grid: days as columns, rows by position.
    assert "Mandag" in html and "Søndag" in html and "2. nov" in html
    assert html.count("data-grid-row") == 1
    assert "Dagskift" in html and "11:00–17:00" in html
    assert "svarte kan ikke" in html and "2 skift denne uka" in html
    assert "1 vil · 0 kan" in html and "0 vil · 1 kan" in html
    # People panel with data attributes for the client side session.
    assert 'data-people-panel' in html
    assert 'data-volunteer-id="4"' in html and 'data-sl="1"' in html
    assert "0 denne uka · 1 denne måneden" in html
    assert "har begynt" in html and "ikke svart" in html
    # Strip and print.
    assert 'data-strip-week="2026-11-09"' in html and "Publisert" in html
    assert "noprint" in html and "data-print-week" in html
    # Data block.
    data = _data(html)
    assert data["bar_id"] == 7 and data["week_start"] == "2026-11-02"
    assert {person["volunteer_id"] for person in data["people"]} == {3, 4}
    shift = next(item for item in data["shifts"] if item["shift_id"] == 1)
    assert shift["answers"] == {"3": "nei", "4": "vil"}
    tora = next(person for person in data["people"] if person["volunteer_id"] == 4)
    assert tora["sl"] is True and "7:skjenker:0" in tora["allowed"]


def test_week_page_has_no_inline_event_handlers_or_inline_scripts_of_its_own() -> None:
    client, _ = _client()
    html = client.get("/shifts/week?group=7").text
    page = html.split('id="week-data"')[0].split("<main", 1)[1]

    assert not re.search(r"\son[a-z]+\s*=", page, re.I)
    assert "javascript:" not in page
    assert re.findall(r"<script\b[^>]*>", html.split("<main", 1)[1]) == [
        '<script type="application/json" id="week-data">',
        re.findall(r'<script defer src="[^"]*shift-week\.js[^"]*">', html)[0],
        re.findall(r'<script defer src="[^"]*shift-week-people\.js[^"]*">', html)[0],
    ]


def test_bar_tabs_only_list_bars_in_the_leaders_scope() -> None:
    client, _ = _client(UserRole.GROUP_ADMIN, GroupScope(frozenset({7})))
    html = client.get("/shifts/week").text

    assert html.count("data-bar-tab") == 1 and "Stjerne" in html
    assert 'data-group-id="8"' not in html


def test_admin_sees_a_tab_for_every_bar() -> None:
    client, _ = _client()
    html = client.get("/shifts/week?group=7").text

    assert html.count("data-bar-tab") == 2
    assert 'href="/shifts/week?week=2026-11-02&amp;group=8"' in html


def test_gruppeadmin_of_one_bar_is_refused_another_bars_group() -> None:
    client, service = _client(UserRole.GROUP_ADMIN, GroupScope(frozenset({7})))
    response = client.get("/shifts/week?group=8")

    assert response.status_code == 403
    assert service.plan_calls == []
    assert service.repository.calls == []


def test_week_navigation_links_keep_the_bar() -> None:
    client, service = _client()
    html = client.get("/shifts/week?week=2026-11-04&group=7").text

    assert 'href="/shifts/week?week=2026-10-26&amp;group=7"' in html
    assert 'href="/shifts/week?week=2026-11-09&amp;group=7"' in html
    assert client.get("/shifts/week?week=2026-11-09&group=7").status_code == 200
    assert (date(2026, 11, 9), 7) in service.plan_calls


def test_bad_query_values_fall_back_to_the_first_bar() -> None:
    client, service = _client()
    response = client.get("/shifts/week?week=nope&group=abc")

    assert response.status_code == 200
    assert service.plan_calls[0][1] == 7


def test_week_page_is_for_managers_only() -> None:
    client, _ = _client(UserRole.VOLUNTEER)

    assert client.get("/shifts/week").status_code == 403


def test_gruppeadmin_without_groups_gets_an_empty_page() -> None:
    client, service = _client(UserRole.GROUP_ADMIN, NO_GROUPS)
    response = client.get("/shifts/week")

    assert response.status_code == 200
    assert "Du er ikke gruppeadmin for noen grupper med skift" in response.text
    assert service.plan_calls == []


def test_unpublished_week_banner_and_links_to_the_old_board() -> None:
    client, service = _client()
    service.banners = [WeekBanner(7, "Stjerne", date(2026, 11, 9), "Uke 46 er ikke publisert.", True, 3)]
    html = client.get("/shifts/week?group=7").text

    assert 'data-week-banner' in html and 'data-banner-level="strong"' in html
    assert "Uke 46 er ikke publisert." in html
    assert 'href="/shifts?week=2026-11-02&amp;group=7"' in html and "Gammel skiftplan" in html


def test_old_board_links_to_the_new_week_page() -> None:
    from tests.web.test_shifts_web import _client as old_client

    client, _ = old_client(UserRole.ADMIN)
    html = client.get("/shifts?week=2026-11-04&group=7").text

    assert 'href="/shifts/week?week=2026-11-02&amp;group=7"' in html and "Ny ukevisning" in html


# --- return_to for the status actions ---


def _post_status(client, return_to: str | None) -> str:
    data = {"group_id": "7", "week": "2026-11-02", "status": "open", "view_group": "7"}
    if return_to is not None:
        data["return_to"] = return_to
    response = client.post(
        "/shifts/weeks/status", data=data, headers=csrf_headers(client), follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"]


def test_status_action_returns_to_a_valid_return_to() -> None:
    from tests.web.test_shifts_web import _client as old_client

    client, _ = old_client(UserRole.ADMIN)
    prime_csrf(client, path="/shifts")

    where = _post_status(client, "/shifts/week?group=7&week=2026-11-02")

    assert where.startswith("/shifts/week?group=7&week=2026-11-02&notice=")


def test_status_action_error_goes_back_to_return_to_too() -> None:
    from tests.web.test_shifts_web import _client as old_client

    client, _ = old_client(UserRole.ADMIN)
    prime_csrf(client, path="/shifts")
    response = client.post(
        "/shifts/weeks/status",
        data={"group_id": "7", "week": "2026-11-02", "status": "nonsense", "return_to": "/shifts/week?group=7&week=2026-11-02"},
        headers=csrf_headers(client),
        follow_redirects=False,
    )

    assert response.headers["location"].startswith("/shifts/week?group=7&week=2026-11-02&error=")


@pytest.mark.parametrize(
    "bad",
    [
        "https://evil.example/shifts",
        "//evil.example/shifts",
        "/\\evil.example",
        "/shifts\\..\\x",
        "/volunteers",
        "/shiftsfoo",
        "/shifts/../volunteers",
        "javascript:alert(1)",
        "shifts/week",
        "/shifts/week#frag",
        "/shifts\n/x",
    ],
)
def test_status_action_ignores_a_return_to_outside_shifts(bad: str) -> None:
    from tests.web.test_shifts_web import _client as old_client

    client, _ = old_client(UserRole.ADMIN)
    prime_csrf(client, path="/shifts")

    where = _post_status(client, bad)

    assert where.startswith("/shifts?week=2026-11-02&group=7&notice=")


def test_status_action_without_return_to_still_redirects_to_the_board() -> None:
    from tests.web.test_shifts_web import _client as old_client

    client, _ = old_client(UserRole.ADMIN)
    prime_csrf(client, path="/shifts")

    assert _post_status(client, None).startswith("/shifts?week=2026-11-02&group=7&notice=")


def test_new_page_sends_return_to_with_its_own_url() -> None:
    client, service = _client()
    service.banners = [WeekBanner(7, "Stjerne", date(2026, 11, 9), "Uke 46 er ikke publisert.", False, 1)]
    html = client.get("/shifts/week?week=2026-11-04&group=7").text

    # Two status buttons and the banner: all return to this bar and the shown week.
    assert html.count('name="return_to" value="/shifts/week?group=7&amp;week=2026-11-02"') == 3


# --- universal design (what can be checked in the HTML) ---


def _page() -> str:
    client, _ = _client()
    return client.get("/shifts/week?week=2026-11-04&group=7").text


def test_grid_is_a_real_table_with_caption_and_scopes() -> None:
    html = _page()

    assert "<caption" in html and "Ukeplan for Stjerne, Uke 45 (2.–8. nov)" in html
    assert html.count('scope="col"') == 8  # corner plus seven days
    assert 'scope="row"' in html
    # A day without a shift says so in text, not only by colour.
    assert re.search(r'data-cell-empty[^>]*><span class="sr-only">Ingen skift</span>', html)


def test_page_has_one_h1_landmarks_and_language() -> None:
    html = _page()

    assert '<html lang="nb"' in html
    assert html.count("<h1") == 1
    for landmark in ("<header", "<main", "<nav", "<footer"):
        assert landmark in html
    assert 'aria-label="Barer"' in html
    # Headings do not skip a level: h1, then h2.
    assert "<h3" not in html.split("<main", 1)[1].split("</main>")[0]


def test_icon_only_controls_name_the_week_they_go_to() -> None:
    html = _page()

    assert 'aria-label="Forrige uke, uke 44"' in html
    assert 'aria-label="Neste uke, uke 46"' in html
    assert '<span aria-hidden="true">‹</span>' in html


def test_problems_have_text_markers_for_screen_readers() -> None:
    html = _page()

    assert '<span class="sr-only">Advarsel: </span>' in html
    assert 'Problem: svarte kan ikke. </span>' in html
    # Answer and counts have text too.
    assert '<span class="sr-only">Svar: </span><span aria-hidden="true">! </span>kan ikke' in html
    assert '<span class="sr-only">Ønsker: </span>1 vil · 0 kan' in html
    assert 'Status: </span>Utkast: ikke synlig i appen' in html


def test_people_panel_scroll_box_is_a_focusable_labelled_region() -> None:
    html = _page()

    assert re.search(r'<div[^>]*role="region"[^>]*aria-label="Liste over folk denne uka"[^>]*tabindex="0"', html)
    assert 'godkjent skiftleder' in html
    assert 'aria-label="Oppsummering av uka"' in html


def test_chips_and_lists_work_without_hover() -> None:
    html = _page()

    # The problem and without-shift lists are details/summary, not title tooltips.
    assert 'data-chip-details="problems"' in html and 'data-chip-details="without-shift"' in html
    assert "<summary" in html and "Vis hvem" in html
    page = html.split("<main", 1)[1]
    assert not re.search(r'\stitle="', page.split('id="week-data"')[0])


def test_current_bar_tab_and_week_are_marked() -> None:
    html = _page()

    assert re.search(r'<a [^>]*aria-current="page"[^>]*data-bar-tab data-group-id="7"', html)
    assert re.search(r'<a [^>]*data-strip-week="2026-11-02"[^>]*aria-current="page"', html)
    assert not re.search(r'data-strip-week="2026-11-09"[^>]*aria-current', html)


def test_disabled_open_button_explains_itself_in_visible_text() -> None:
    client, service = _client()
    service.can_open = False
    html = client.get("/shifts/week?group=7").text

    assert "Bare kommende uker kan åpnes for ønsker." in html


def _grid(html: str) -> str:
    return html.split('id="week-grid"', 1)[1].split("</section>", 1)[0]


def test_answered_place_has_dot_visible_word_and_hidden_text() -> None:
    grid = _grid(_page())

    assert re.search(
        r'<span class="week-answer" data-answer="nei"><span class="week-dot week-dot-nei" aria-hidden="true"></span>'
        r'<span class="sr-only">Svar: </span><span aria-hidden="true">! </span>kan ikke</span>',
        grid,
    )
    assert re.search(
        r'<span class="week-answer" data-answer="kan"><span class="week-dot week-dot-kan" aria-hidden="true"></span>'
        r'<span class="sr-only">Svar: </span>kan</span>',
        grid,
    )
    assert 'week-dot-kan' in grid
    # Boxes stay untinted for answers: only problems tint a place.
    assert "week-place-yellow" in grid and "week-place-red" in grid
    assert "week-answer-" not in grid


def test_ikke_svart_is_not_in_the_grid_but_is_in_the_people_panel() -> None:
    client, service = _client()

    async def no_wishes(shift_ids, volunteer_ids):
        return {}

    service.repository.list_wishes_for = no_wishes
    html = client.get("/shifts/week?week=2026-11-02&group=7").text
    grid = _grid(html)

    assert "ikke svart" not in grid and "week-answer" not in grid and "week-dot" not in grid
    assert "Sander Skjenker" in grid
    panel = html.split("data-people-panel", 1)[1]
    assert panel.count("ikke svart") == 1  # Sander; Tora has begun


def test_kan_ikke_answer_has_hidden_problem_text_and_no_separate_visible_line() -> None:
    grid = _grid(_page())
    first_cell = grid.split("data-cell ", 2)[1]

    assert '<span class="sr-only" data-problem="red">Problem: svarte kan ikke. </span>' in first_cell
    assert 'data-problem="red"><span class="sr-only">Problem: </span>' not in first_cell
    assert "! svarte kan ikke" not in grid
    assert 'week-place-red' in first_cell
    # Other problems stay visible, in short form.
    assert '<span aria-hidden="true">! </span>2 skift denne uka' in grid


def test_people_rows_are_two_lines_with_hidden_full_text() -> None:
    html = _page()
    panel = html.split("data-people-panel-wrap", 1)[1]

    assert '<span aria-hidden="true">0 uka · 1 mnd</span><span class="sr-only">0 denne uka · 1 denne måneden</span>' in panel
    assert "whitespace-nowrap" in panel
    assert re.search(r'data-person data-volunteer-id="4" data-name="tora tilgjengelig" data-sl="1"', panel)


def test_people_panel_is_a_sticky_wrapper_with_a_labelled_focusable_region() -> None:
    html = _page()

    assert 'data-people-panel-wrap' in html
    assert re.search(r'role="region" aria-label="Liste over folk denne uka" tabindex="0" data-people-region', html)


def test_status_buttons_sit_in_the_week_bar_before_the_print_button() -> None:
    bar = _page().split("data-week-bar", 1)[1].split("data-week-topline", 1)[0]

    assert bar.index("data-week-status") < bar.index("data-print-week")
    assert "ml-auto" in bar


def _grid_head(shift_days: list[int]) -> str:
    client, service = _client()
    service.shift_days = shift_days
    html = client.get("/shifts/week?week=2026-11-02&group=7").text
    return _grid(html)


def test_days_without_any_shift_collapse_but_keep_their_columns() -> None:
    grid = _grid_head([2, 3, 4, 5, 6])  # Monday to Friday

    assert grid.count('scope="col"') == 8
    assert grid.count('data-collapsed="true"') == 4  # two headers and two empty cells
    assert re.search(r'data-day="2026-11-07"[^>]*data-collapsed="true">.*?>Lø</span>.*?<span class="sr-only">Lørdag 7. nov</span>', grid, re.S)
    assert re.search(r'data-day="2026-11-08"[^>]*data-collapsed="true">.*?>Sø</span>.*?<span class="sr-only">Søndag 8. nov</span>', grid, re.S)
    assert len(re.findall(r'data-cell-empty data-collapsed="true"><span class="sr-only">Ingen skift</span>', grid)) == 2
    assert 'data-used-days="5"' in grid
    # Days with shifts keep the full header.
    assert '<span class="block font-semibold">Mandag</span>' in grid and 'data-day="2026-11-06"' in grid
    assert len(re.findall(r"<col[ >]", grid)) == 8


def test_a_bar_with_all_seven_days_has_no_collapsed_column() -> None:
    grid = _grid_head([2, 3, 4, 5, 6, 7, 8])

    assert "data-collapsed" not in grid
    assert 'data-used-days="7"' in grid and grid.count('scope="col"') == 8
    assert 'scope="row"' in grid


def _panel(html: str) -> str:
    return html.split("data-people-panel-wrap", 1)[1].split("data-people-region", 1)[0]


def test_search_input_has_a_visible_label() -> None:
    panel = _panel(_page())

    assert re.search(r'<label[^>]*for="week-people-search"[^>]*>Søk etter navn</label>', panel)
    assert re.search(r'<input[^>]*id="week-people-search"[^>]*type="search"', panel)


def test_filter_buttons_are_toggle_buttons_with_short_text_and_full_names() -> None:
    panel = _panel(_page())

    for key, visible, full in (
        ("sl", "Bare SL", "Bare skiftledere"),
        ("unanswered", "Ikke svart", None),
        ("noshift", "Uten skift", "Uten skift denne uka"),
    ):
        match = re.search(rf'<button[^>]*type="button"[^>]*aria-pressed="false"[^>]*data-filter="{key}">(.*?)</button>', panel)
        assert match and visible in match.group(1)
        if full:
            assert f'<span class="sr-only">{full}</span>' in match.group(1)
    assert re.search(r'<button[^>]*type="button"[^>]*hidden[^>]*data-people-reset>Nullstill</button>', panel)


def test_count_is_a_polite_live_region_with_the_empty_text() -> None:
    panel = _panel(_page())

    match = re.search(r'<p[^>]*aria-live="polite"[^>]*aria-atomic="true"[^>]*data-people-count>(.*?)</p>', panel, re.S)
    assert match and "Viser 2 av 2" in match.group(1)
    assert re.search(r"<span data-people-empty hidden> Ingen treff</span>", match.group(1))


def test_every_person_row_has_a_button_with_the_name_and_aria_pressed() -> None:
    html = _page()
    rows = re.findall(r"<li [^>]*data-person .*?</li>", html.split("data-people-list", 1)[1], re.S)

    assert len(rows) == 2
    for row in rows:
        assert re.search(r'<button[^>]*type="button"[^>]*aria-pressed="false"[^>]*data-person-button>[^<]+</button>', row)
    assert re.search(r'data-person-button>Tora Tilgjengelig</button>', html)
    assert 'class="week-selected-tag' in html and ">Valgt<" in html


def test_selection_line_is_hidden_until_used_and_has_fjern_valg() -> None:
    html = _page()

    assert re.search(r'<p[^>]*data-selection hidden>Viser: <strong data-selection-name></strong>', html)
    assert re.search(r'<button[^>]*type="button"[^>]*data-selection-clear>Fjern valg</button>', html)
    assert "noprint" in re.search(r'<p[^>]*data-selection hidden', html).group(0)


def test_fold_button_controls_the_panel_and_both_are_noprint_where_needed() -> None:
    html = _page()
    button = re.search(r"<button[^>]*data-people-toggle>Skjul folk</button>", html).group(0)
    target = re.search(r'aria-controls="([^"]+)"', button).group(1)

    assert 'aria-expanded="true"' in button
    assert len(re.findall(rf'id="{target}"', html)) == 1
    assert re.search(rf'<div class="[^"]*noprint[^"]*" id="{target}"', html)
    assert re.search(r'<div class="noprint ml-auto[^"]*">\s*<button[^>]*data-people-toggle', html)
    assert 'data-people-folded="false"' in html and "data-week-layout" in html


def test_new_controls_are_in_keyboard_order_search_filters_list_then_grid_after_panel_in_source() -> None:
    html = _page()

    # Source order is the tab order: search, filters, reset, people list; the grid comes first in the
    # layout, so its content is reached before the panel only through its own (non-focusable) cells.
    panel = html.split("data-people-panel-wrap", 1)[1]
    assert panel.index("data-people-search") < panel.index("data-filter=") < panel.index("data-people-reset") < panel.index("data-person-button")


def test_no_inline_handlers_in_the_new_controls() -> None:
    html = _page()
    page = html.split('id="week-data"')[0].split("<main", 1)[1]

    assert not re.search(r"\son[a-z]+\s*=", page, re.I)
    assert "style=" not in _panel(html)


def _bar_html(**overrides) -> str:
    from types import SimpleNamespace

    from app.web.templates import templates

    view = _view_for_bar(**overrides)
    return templates.env.get_template("pages/shifts/_week_bar.html").render(view=view, page=SimpleNamespace(status=None))


def _view_for_bar(*, count: int, red: int):
    from datetime import date as _date
    from types import SimpleNamespace

    return SimpleNamespace(
        bar_id=7,
        previous_week=_date(2026, 10, 26),
        next_week=_date(2026, 11, 9),
        week_label="Uke 45",
        status="open",
        status_text="Åpen",
        problem_count=count,
        problem_red_count=red,
        problem_places=[f"2. nov, Dagskift: Person {i}" for i in range(count)],
        open_places=0,
        without_shift=[],
    )


def test_problem_chip_is_red_and_says_problemer_when_a_red_problem_exists() -> None:
    html = _bar_html(count=3, red=2)

    assert re.search(r'class="week-chip week-chip-red" data-chip="problems"><span aria-hidden="true">! </span>2 problemer</summary>', html)
    assert html.count("<li>2. nov") == 3  # the list still has every place


def test_problem_chip_is_yellow_and_says_advarsler_when_only_warnings() -> None:
    html = _bar_html(count=2, red=0)

    assert re.search(r'class="week-chip week-chip-yellow" data-chip="problems"><span aria-hidden="true">! </span>2 advarsler</summary>', html)
    assert "week-chip-red" not in html
    assert "1 advarsel<" in _bar_html(count=1, red=0)


def test_problem_chip_is_plain_text_when_there_are_no_problems() -> None:
    html = _bar_html(count=0, red=0)

    assert '<span class="week-chip" data-chip="problems">0 problemer</span>' in html
    assert 'data-chip-details="problems"' not in html
