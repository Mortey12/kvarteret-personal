"""Placing people from the week page (L2a): the picker, put, swap and remove.

The real ``ShiftsService`` runs on an in-memory repository (``tests/support/week_placing.py``), so the
hard rules, the lock and the "kan ikke i dag" path are the real ones. Made-up people only.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth.roles import UserRole
from app.dependencies import get_shifts_service
from app.domain.shifts.models import SlotRecord
from app.domain.shifts.service import ShiftsService
from app.main import create_app
from tests.support.helpers import csrf_headers, make_authenticated_user, override_authenticated_user, prime_csrf
from tests.support.week_placing import (
    HALLE,
    SANDER,
    SECOND_SHIFT_SLOT,
    SIGRID,
    SKIFTLEDER_SLOT,
    SKJENKER_SLOT,
    TORA,
    UNDINE,
    PlacingRepository,
)

WEEK = "2026-11-02"
FRAGMENT = {"X-Week-Fragment": "1"}


def _client(role: UserRole = UserRole.ADMIN, repo: PlacingRepository | None = None):
    repo = repo or PlacingRepository()
    app = create_app()
    override_authenticated_user(app, make_authenticated_user(role))
    service = ShiftsService(repo, now=lambda: datetime(2026, 11, 2, 12, tzinfo=UTC))
    app.dependency_overrides[get_shifts_service] = lambda: service
    client = TestClient(app)
    prime_csrf(client, path="/")
    return client, repo


def _post(client, path: str, fragment: bool = True, **data):
    data.setdefault("group", "7")
    data.setdefault("week", WEEK)
    headers = {**csrf_headers(client), **(FRAGMENT if fragment else {})}
    return client.post(path, data={k: str(v) for k, v in data.items()}, headers=headers, follow_redirects=False)


def _names(html: str) -> list[str]:
    return re.findall(r'data-picker-row data-volunteer-id="\d+" data-name="([^"]*)"', html)


def _message(html: str) -> str:
    return re.search(r"<p data-live-text>(.*?)</p>", html, re.S).group(1)


# ------------------------------------------------------------------ the picker

def test_picker_lists_only_people_who_fit_sorted_by_answer_then_load() -> None:
    client, repo = _client()
    repo.wishes = {(1, SANDER): "vil", (1, HALLE): "kan", (1, UNDINE): "nei"}
    html = client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").text

    # Tora is in training (the crew place is not hers); Sigrid has no answer; Undine answered kan ikke.
    assert _names(html) == ["sander skjenker", "halle hjelper", "sigrid skiftleder", "undine under"]
    assert "Sett inn som skjenker, tirsdag 3. nov., Dagskift" in html
    assert 'id="week-picker-title"' in html and "Søk etter navn" in html


def test_picker_ties_go_to_fewer_shifts_this_week_then_this_month_then_name() -> None:
    client, repo = _client()
    repo.wishes = {(1, SANDER): "vil", (1, HALLE): "vil", (1, SIGRID): "vil", (1, UNDINE): "vil"}
    repo.shifts[3] = repo.shifts[2].__class__(
        3, 7, "Stjerne", "Stjernesalen", "Onsdag", datetime(2026, 11, 4, 16, tzinfo=UTC),
        datetime(2026, 11, 4, 22, tzinfo=UTC), None, False, False, [SlotRecord(31, "skjenker", 2, False, 0, [])],
    )
    repo.place(31, SANDER)  # Sander has one shift this week
    names = _names(client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").text)

    assert names == ["halle hjelper", "sigrid skiftleder", "undine under", "sander skjenker"]


def test_skiftleder_place_lists_only_approved_skiftledere() -> None:
    client, _ = _client()
    html = client.get(f"/shifts/week/picker?slot_id={SKIFTLEDER_SLOT}").text

    assert _names(html) == ["sigrid skiftleder"]
    assert "SL" in html


def test_picker_rows_show_dot_word_counts_and_warnings_as_text() -> None:
    client, repo = _client()
    repo.wishes = {(1, SANDER): "vil", (1, UNDINE): "nei"}
    repo.place(SECOND_SHIFT_SLOT, SANDER)  # Sander is on the evening shift the same day
    html = client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").text
    sander = html.split('data-name="sander skjenker"')[1].split("</li>")[0]
    undine = html.split('data-name="undine under"')[1].split("</li>")[0]

    assert "week-pick-red" in html.split('data-name="sander skjenker"')[0].rsplit("<li", 1)[1]
    assert "to skift samme dag" in sander and "skift nummer 2 denne uka" in sander
    assert 'data-problem="red"' in sander and 'data-problem="yellow"' in sander
    assert "Problem: </span>" in sander or '<span class="sr-only">Problem: </span>' in sander
    assert "1 uka" in sander and "vil" in sander and "week-dot-vil" in sander
    assert "svarte kan ikke" in undine and "kan ikke" in undine
    # Not answered: no word for it.
    sigrid = html.split('data-name="sigrid skiftleder"')[1].split("</li>")[0]
    assert "ikke svart" not in sigrid and "Svar:" not in sigrid


def test_picker_nobody_fits_and_search_empty_texts_exist() -> None:
    client, repo = _client()
    repo.qualifications = []
    html = client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").text

    assert _names(html) == [] and "Ingen i listen passer denne plassen." in html
    assert "Ingen treff" in html  # the hidden search-empty text the picker script shows


def test_picker_leaves_out_people_already_on_the_shift_and_the_closed_shift_is_refused() -> None:
    client, repo = _client()
    repo.place(SKJENKER_SLOT, SANDER)
    assert "sander skjenker" not in _names(client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").text)

    repo.shifts[1].is_closed = True
    refused = client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}")
    assert refused.status_code == 409 and "Skiftet er stengt." in refused.json()["detail"]


def test_picker_for_a_swap_names_the_old_person_and_does_not_list_them() -> None:
    client, repo = _client()
    old = repo.place(SKJENKER_SLOT, SANDER)
    repo.place(SKJENKER_SLOT, HALLE)  # the place is full: only the swap makes room
    html = client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}&replace={old}").text

    assert "Bytt ut Sander Skjenker som skjenker, tirsdag 3. nov., Dagskift" in html
    assert 'action="/shifts/week/replace"' in html and f'name="assignment_id" value="{old}"' in html
    assert "sander skjenker" not in _names(html) and "halle hjelper" not in _names(html)
    assert "undine under" in _names(html)
    # A plain picker for a full place is refused.
    assert client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").status_code == 409


def test_picker_says_who_comes_off_when_a_kan_ikke_i_dag_place_is_taken() -> None:
    client, repo = _client()
    repo.place(SKJENKER_SLOT, SANDER, absent=True)
    repo.place(SKJENKER_SLOT, HALLE)
    html = client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").text

    assert "Sander Skjenker har meldt kan ikke i dag" in html and "tas av" in html


def test_picker_is_refused_for_a_group_outside_the_scope_and_a_missing_place() -> None:
    repo = PlacingRepository()
    repo.admin_group_ids = [8]
    client, _ = _client(UserRole.GROUP_ADMIN, repo)
    assert client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").status_code == 403
    client, _ = _client()
    assert client.get("/shifts/week/picker?slot_id=999").status_code == 409


# ------------------------------------------------------------------ put

def test_put_places_the_person_and_answers_with_the_refreshed_week_and_a_message() -> None:
    client, repo = _client()
    response = _post(client, "/shifts/week/place", slot_id=SKJENKER_SLOT, volunteer_id=SANDER)

    assert response.status_code == 200
    assert repo.who(SKJENKER_SLOT) == [SANDER]
    html = response.text
    assert "Sander Skjenker er satt opp som skjenker" in _message(html)
    assert 'data-level="ok"' in html and f'data-focus-slot="{SKJENKER_SLOT}"' in html
    assert f'data-focus-volunteer="{SANDER}"' in html
    # The changed parts of the week come back: grid, status, chips, people list and the data block.
    for part in ("data-week-grid", "data-week-status-badge", "data-week-chips", "data-people-list", 'id="week-data"'):
        assert part in html
    assert "Sander Skjenker" in html.split("data-week-grid")[1]


def test_put_warnings_do_not_block_and_are_in_the_message() -> None:
    client, repo = _client()
    repo.wishes = {(1, UNDINE): "nei"}
    repo.place(SECOND_SHIFT_SLOT, UNDINE)
    response = _post(client, "/shifts/week/place", slot_id=SKJENKER_SLOT, volunteer_id=UNDINE)

    assert response.status_code == 200 and UNDINE in repo.who(SKJENKER_SLOT)
    message = _message(response.text)
    assert "Advarsel:" in message and "svarte kan ikke" in message and "to skift samme dag" in message
    assert 'data-level="warn"' in response.text
    assert "Merk:" not in message


def test_put_without_javascript_redirects_back_with_a_notice_or_an_error() -> None:
    client, repo = _client()
    ok = _post(client, "/shifts/week/place", fragment=False, slot_id=SKJENKER_SLOT, volunteer_id=SANDER)
    assert ok.status_code == 303 and ok.headers["location"].startswith("/shifts/week?week=2026-11-02&group=7&notice=")
    bad = _post(client, "/shifts/week/place", fragment=False, slot_id=SKIFTLEDER_SLOT, volunteer_id=SANDER)
    assert bad.status_code == 303 and "error=" in bad.headers["location"]
    assert repo.who(SKIFTLEDER_SLOT) == []


@pytest.mark.parametrize(
    ("setup", "slot", "who", "expected"),
    [
        ("closed", SKJENKER_SLOT, SANDER, "Skiftet er stengt."),
        ("twice", SKJENKER_SLOT, SANDER, "Personen står allerede på dette skiftet."),
        ("full", SKJENKER_SLOT, UNDINE, "Plassen er full."),
        ("not_sl", SKIFTLEDER_SLOT, SANDER, "Kun for godkjente skiftledere."),
        ("trainee", SKJENKER_SLOT, TORA, "I opplæring: bruk opplæringsplassen."),
        ("outside_pool", SKJENKER_SLOT, 900, "Fann ikke personen i gruppa."),
        ("young", 31, SANDER, "Halvtimen krever 20 år."),
    ],
)
def test_each_hard_rule_is_refused_with_the_reason_and_the_refreshed_week(setup, slot, who, expected) -> None:
    client, repo = _client()
    repo.shifts[3] = repo.shifts[2].__class__(
        3, 7, "Stjerne", "Halvtimen", "Halvtime", datetime(2026, 11, 5, 16, tzinfo=UTC),
        datetime(2026, 11, 5, 22, tzinfo=UTC), None, False, False, [SlotRecord(31, "skjenker", 2, False, 0, [])],
    )
    repo.birth_dates[SANDER] = date(2010, 5, 1)
    if setup == "closed":
        repo.shifts[1].is_closed = True
    if setup == "twice":
        repo.place(SKJENKER_SLOT, SANDER)
    if setup == "full":
        repo.place(SKJENKER_SLOT, SANDER)
        repo.place(SKJENKER_SLOT, HALLE)
    before = {s: repo.who(s) for s in (SKIFTLEDER_SLOT, SKJENKER_SLOT, 31)}
    response = _post(client, "/shifts/week/place", slot_id=slot, volunteer_id=who)

    assert response.status_code == 409
    assert expected.rstrip(".") in _message(response.text) and _message(response.text).startswith("Ikke lagret:")
    assert 'data-level="error"' in response.text and "data-week-grid" in response.text
    assert {s: repo.who(s) for s in before} == before


def test_halvtimen_with_unknown_birth_date_is_a_warning_not_a_refusal() -> None:
    client, repo = _client()
    repo.shifts[3] = repo.shifts[2].__class__(
        3, 7, "Stjerne", "Halvtimen", "Halvtime", datetime(2026, 11, 5, 16, tzinfo=UTC),
        datetime(2026, 11, 5, 22, tzinfo=UTC), None, False, False, [SlotRecord(31, "skjenker", 2, False, 0, [])],
    )
    response = _post(client, "/shifts/week/place", slot_id=31, volunteer_id=SANDER)

    assert response.status_code == 200 and repo.who(31) == [SANDER]
    assert "fødselsdato mangler" in _message(response.text)


def test_taking_a_kan_ikke_i_dag_place_takes_the_absent_person_off() -> None:
    client, repo = _client()
    repo.place(SECOND_SHIFT_SLOT, SANDER, absent=True)
    repo.place(SECOND_SHIFT_SLOT, HALLE)
    # The place counts as open: it is in the grid as "Ledig" and in the picker.
    page = client.get(f"/shifts/week?group=7&week={WEEK}").text
    assert f'data-place-open data-slot-id="{SECOND_SHIFT_SLOT}"' in page
    picker = client.get(f"/shifts/week/picker?slot_id={SECOND_SHIFT_SLOT}")
    assert picker.status_code == 200

    response = _post(client, "/shifts/week/place", slot_id=SECOND_SHIFT_SLOT, volunteer_id=UNDINE)

    assert response.status_code == 200
    assert sorted(repo.who(SECOND_SHIFT_SLOT)) == sorted([HALLE, UNDINE])


def test_put_for_a_forbidden_group_is_403_and_changes_nothing() -> None:
    repo = PlacingRepository()
    repo.admin_group_ids = [8]
    client, _ = _client(UserRole.GROUP_ADMIN, repo)
    response = _post(client, "/shifts/week/place", slot_id=SKJENKER_SLOT, volunteer_id=SANDER)

    assert response.status_code == 403 and repo.who(SKJENKER_SLOT) == []


# ------------------------------------------------------------------ swap

def test_swap_replaces_the_person_in_one_request() -> None:
    client, repo = _client()
    old = repo.place(SKJENKER_SLOT, SANDER)
    repo.place(SKJENKER_SLOT, HALLE)  # full place
    response = _post(client, "/shifts/week/replace", assignment_id=old, slot_id=SKJENKER_SLOT, volunteer_id=UNDINE)

    assert response.status_code == 200
    assert sorted(repo.who(SKJENKER_SLOT)) == sorted([HALLE, UNDINE])
    assert "Undine Under er satt opp som skjenker" in _message(response.text)
    assert "i stedet for Sander Skjenker" in _message(response.text)
    assert repo.events[-3:] == [("lock", 1), ("remove", old), ("add", SKJENKER_SLOT, UNDINE)]


def test_a_refused_swap_leaves_the_old_person_in_place() -> None:
    client, repo = _client()
    old = repo.place(SKJENKER_SLOT, SANDER)
    response = _post(client, "/shifts/week/replace", assignment_id=old, slot_id=SKJENKER_SLOT, volunteer_id=TORA)

    assert response.status_code == 409 and "I opplæring" in _message(response.text)
    assert repo.who(SKJENKER_SLOT) == [SANDER]
    assert not any(event[0] == "remove" for event in repo.events)


def test_swap_on_a_skiftleder_place_needs_an_approved_skiftleder() -> None:
    client, repo = _client()
    old = repo.place(SKIFTLEDER_SLOT, SIGRID)
    refused = _post(client, "/shifts/week/replace", assignment_id=old, slot_id=SKIFTLEDER_SLOT, volunteer_id=SANDER)
    assert refused.status_code == 409 and repo.who(SKIFTLEDER_SLOT) == [SIGRID]


def test_swap_with_the_same_person_or_a_vanished_assignment_is_friendly() -> None:
    client, repo = _client()
    old = repo.place(SKJENKER_SLOT, SANDER)
    same = _post(client, "/shifts/week/replace", assignment_id=old, slot_id=SKJENKER_SLOT, volunteer_id=SANDER)
    assert same.status_code == 200 and "Ingen endring" in _message(same.text) and repo.who(SKJENKER_SLOT) == [SANDER]
    gone = _post(client, "/shifts/week/replace", assignment_id=9999, slot_id=SKJENKER_SLOT, volunteer_id=UNDINE)
    assert gone.status_code == 409 and "finnes ikke lenger" in _message(gone.text)


# ------------------------------------------------------------------ remove

def test_remove_takes_the_person_off_and_the_place_is_open_again() -> None:
    client, repo = _client()
    old = repo.place(SKJENKER_SLOT, SANDER)
    response = _post(client, "/shifts/week/remove", assignment_id=old, slot_id=SKJENKER_SLOT)

    assert response.status_code == 200 and repo.who(SKJENKER_SLOT) == []
    assert "Sander Skjenker er tatt av" in _message(response.text) and "Plassen er ledig igjen" in _message(response.text)
    assert 'data-focus-volunteer=""' in response.text


def test_remove_of_a_place_taken_meanwhile_is_a_friendly_message_with_the_refreshed_week() -> None:
    client, _ = _client()
    response = _post(client, "/shifts/week/remove", assignment_id=4242, slot_id=SKJENKER_SLOT)

    assert response.status_code == 409 and "finnes ikke lenger" in _message(response.text)
    assert "data-week-grid" in response.text


def test_remove_for_a_forbidden_group_is_403() -> None:
    repo = PlacingRepository()
    old = repo.place(SKJENKER_SLOT, SANDER)
    repo.admin_group_ids = [8]
    client, _ = _client(UserRole.GROUP_ADMIN, repo)
    assert _post(client, "/shifts/week/remove", assignment_id=old, slot_id=SKJENKER_SLOT).status_code == 403
    assert repo.who(SKJENKER_SLOT) == [SANDER]


# ------------------------------------------------------------------ CSRF, role, log

@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/shifts/week/place", {"slot_id": "11", "volunteer_id": "101", "group": "7", "week": WEEK}),
        ("/shifts/week/replace", {"assignment_id": "1", "slot_id": "11", "volunteer_id": "101", "group": "7", "week": WEEK}),
        ("/shifts/week/remove", {"assignment_id": "1", "slot_id": "11", "group": "7", "week": WEEK}),
    ],
)
def test_actions_need_csrf_and_a_management_role(path, data) -> None:
    client, repo = _client()
    client.cookies.set(client.app.state.container.settings.session_cookie_name, "signed-in")
    assert client.post(path, data=data).status_code == 403

    volunteer, _ = _client(UserRole.VOLUNTEER)
    assert volunteer.post(path, data=data, headers=csrf_headers(volunteer)).status_code == 403
    assert volunteer.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").status_code == 403
    assert repo.who(SKJENKER_SLOT) == []


def test_each_action_is_logged_as_admin_activity_only_on_success(monkeypatch) -> None:
    logged: list[dict] = []
    monkeypatch.setattr("app.web.routes.shifts.week_actions.log_admin_activity", lambda **kw: logged.append(kw))
    client, repo = _client()

    _post(client, "/shifts/week/place", slot_id=SKJENKER_SLOT, volunteer_id=SANDER)
    _post(client, "/shifts/week/place", slot_id=SKIFTLEDER_SLOT, volunteer_id=SANDER)  # refused: not logged
    old = repo.place(SKJENKER_SLOT, HALLE)
    _post(client, "/shifts/week/replace", assignment_id=old, slot_id=SKJENKER_SLOT, volunteer_id=UNDINE)
    new = next(a for a, ref in repo.assignments.items() if ref.volunteer_id == UNDINE)
    _post(client, "/shifts/week/remove", assignment_id=new, slot_id=SKJENKER_SLOT)

    assert [(item["action"], item["subject_type"]) for item in logged] == [
        ("shift.assign", "shift_slot"),
        ("shift.replace", "shift_assignment"),
        ("shift.unassign", "shift_assignment"),
    ]
    assert logged[0]["subject_id"] == SKJENKER_SLOT and logged[0]["details"] == {"volunteer_id": SANDER}


# ------------------------------------------------------------------ the page markup

def _page(repo: PlacingRepository | None = None) -> str:
    client, _ = _client(repo=repo)
    return client.get(f"/shifts/week?group=7&week={WEEK}").text


def _data(html: str) -> dict:
    return json.loads(re.search(r'id="week-data">(.*?)</script>', html, re.S).group(1))


def test_open_places_are_buttons_with_text_and_the_dialog_aria() -> None:
    html = _page()
    button = re.search(r'<button[^>]*data-place-open[^>]*>.*?</button>', html, re.S).group(0)

    assert 'type="button"' in button and 'aria-haspopup="dialog"' in button and 'aria-expanded="false"' in button
    assert "Ledig" in button and "sett inn en person som skiftleder, tirsdag 3. nov." in button
    assert 'aria-describedby="place-extra-' in button
    dialog = re.search(r"<dialog[^>]*>", html).group(0)
    assert 'aria-labelledby="week-picker-title"' in dialog and 'hx-boost="false"' in dialog and "noprint" in dialog


def test_filled_places_have_a_disclosure_with_swap_and_remove() -> None:
    repo = PlacingRepository()
    assignment = repo.place(SKJENKER_SLOT, SANDER)
    html = _page(repo)
    toggle = re.search(r'<button[^>]*data-place-menu-toggle[^>]*>.*?</button>', html, re.S).group(0)

    assert "Endre" in toggle and 'aria-expanded="false"' in toggle and f'aria-controls="place-menu-{assignment}"' in toggle
    assert "Sander Skjenker" in toggle  # the hidden text names the person
    menu = re.search(rf'<div[^>]*id="place-menu-{assignment}"[^>]*>.*?</form>', html, re.S).group(0)
    assert "hidden" in menu.split(">")[0] and "noprint" in menu.split(">")[0]
    assert "Bytt person" in menu and "Fjern fra plassen" in menu
    assert 'action="/shifts/week/remove"' in menu and 'name="csrf_token"' in menu
    assert f'data-assignment-id="{assignment}"' in menu and 'aria-haspopup="dialog"' in menu


def test_page_has_a_polite_live_region_and_the_new_script_and_no_inline_handlers() -> None:
    html = _page()
    live = re.search(r"<p[^>]*data-week-live[^>]*>", html).group(0)
    main = html.split("<main", 1)[1]

    assert 'role="status"' in live and 'aria-live="polite"' in live and 'aria-atomic="true"' in live
    assert re.search(r'<script defer src="[^"]*shift-week-place\.js', html)
    assert not re.search(r"\son[a-z]+\s*=", main, re.I) and "javascript:" not in main
    assert 'data-group-id="7"' in html and f'data-week-start="{WEEK}"' in html


def test_every_new_control_is_a_real_button_or_form_and_forms_opt_out_of_boosting() -> None:
    repo = PlacingRepository()
    repo.place(SKJENKER_SLOT, SANDER)
    html = _page(repo)
    grid = html.split("data-week-grid", 1)[1].split("</section>")[0]

    assert "<div role=\"button\"" not in grid and "tabindex=\"0\"" not in grid
    for form in re.findall(r"<form[^>]*data-place-form[^>]*>", grid):
        assert 'method="post"' in form and 'hx-boost="false"' in form


def test_picker_markup_has_label_search_close_and_text_for_every_signal() -> None:
    client, repo = _client()
    repo.wishes = {(1, SANDER): "vil", (1, UNDINE): "nei"}
    html = client.get(f"/shifts/week/picker?slot_id={SKJENKER_SLOT}").text

    assert re.search(r'<label[^>]*for="week-picker-search"', html) and 'id="week-picker-search"' in html
    assert 'aria-live="polite"' in html and "data-picker-close" in html and ">Lukk<" in html
    assert 'class="week-dot' in html and ">vil<" in html and ">kan ikke<" in html
    assert 'hx-boost="false"' in html and 'name="csrf_token"' in html
    # Warnings are text with a hidden "Problem:" or "Advarsel:" prefix; the "!" is decorative.
    assert re.search(r'<span aria-hidden="true">! </span>svarte kan ikke', html)


# ------------------------------------------------------------------ person first (the data block)

def test_week_data_carries_the_person_first_state_decided_by_the_server() -> None:
    repo = PlacingRepository()
    repo.place(SKJENKER_SLOT, SANDER)
    repo.place(SECOND_SHIFT_SLOT, HALLE)
    repo.wishes = {(1, UNDINE): "nei"}
    state = _data(_page(repo))["place_state"]
    skjenker, skiftleder = str(SKJENKER_SLOT), str(SKIFTLEDER_SLOT)

    # Someone who fits, with a warning shown before the click.
    undine = state[str(UNDINE)]
    assert undine[skjenker]["ok"] == 1 and ["svarte kan ikke", "red"] in undine[skjenker]["w"]
    # A skiftleder place: only the approved skiftleder fits, the others get the reason.
    assert state[str(SIGRID)][skiftleder]["ok"] == 1
    assert state[str(UNDINE)][skiftleder] == {"ok": 0, "why": "Kun for godkjente skiftledere"}
    # Hard rules show as reasons: in training, already on the shift.
    assert state[str(TORA)][skjenker] == {"ok": 0, "why": "I opplæring: bruk opplæringsplassen"}
    assert state[str(SANDER)][skjenker] == {"ok": 0, "why": "Står allerede på dette skiftet"}
    # Another shift the same day and the week count are warnings, not reasons.
    sander_second = state[str(SANDER)][str(SECOND_SHIFT_SLOT)]
    assert sander_second["ok"] == 1
    assert ["to skift samme dag", "red"] in sander_second["w"] and ["skift nummer 2 denne uka", "yellow"] in sander_second["w"]


def test_closed_shifts_have_no_open_places_and_so_no_state() -> None:
    repo = PlacingRepository()
    repo.shifts[1].is_closed = True
    state = _data(_page(repo))["place_state"]

    assert str(SKJENKER_SLOT) not in state[str(SANDER)] and str(SECOND_SHIFT_SLOT) in state[str(SANDER)]


# ------------------------------------------------------------------ the group comes from the server

def _other_bar(repo: PlacingRepository) -> int:
    """A place in Kjøkken (8), outside a leader who only leads Stjerne (7)."""
    repo.shifts[9] = repo.shifts[2].__class__(
        9, 8, "Kjøkken", "Stjernesalen", "Kjøkkenskift", datetime(2026, 11, 3, 16, tzinfo=UTC),
        datetime(2026, 11, 3, 22, tzinfo=UTC), None, False, False, [SlotRecord(91, "skjenker", 2, False, 0, [])],
    )
    return 91


def test_picker_refuses_a_place_outside_the_scope_before_any_name_is_read_whatever_the_request_says() -> None:
    repo = PlacingRepository()
    slot = _other_bar(repo)
    client, _ = _client(UserRole.GROUP_ADMIN, repo)
    reads = repo.member_reads
    response = client.get(f"/shifts/week/picker?slot_id={slot}&group=7&week={WEEK}")

    assert response.status_code == 403 and repo.member_reads == reads
    assert "Sander" not in response.text


def test_actions_use_the_places_own_group_not_the_group_in_the_request() -> None:
    repo = PlacingRepository()
    slot = _other_bar(repo)
    held = repo.place(slot, SANDER)
    client, _ = _client(UserRole.GROUP_ADMIN, repo)  # leads Stjerne only; the request claims group 7
    for path, data in (
        ("/shifts/week/place", {"slot_id": slot, "volunteer_id": HALLE}),
        ("/shifts/week/replace", {"assignment_id": held, "slot_id": slot, "volunteer_id": HALLE}),
        ("/shifts/week/remove", {"assignment_id": held, "slot_id": slot}),
    ):
        assert _post(client, path, group=7, **data).status_code == 403
    assert repo.who(slot) == [SANDER]


def test_replace_and_remove_check_that_the_assignment_is_on_the_given_place() -> None:
    client, repo = _client()
    held = repo.place(SKJENKER_SLOT, SANDER)
    for path, data in (
        ("/shifts/week/replace", {"volunteer_id": UNDINE}),
        ("/shifts/week/remove", {}),
    ):
        response = _post(client, path, assignment_id=held, slot_id=SECOND_SHIFT_SLOT, **data)
        assert response.status_code == 409 and "Plassen er endret" in _message(response.text)
    assert repo.who(SKJENKER_SLOT) == [SANDER] and repo.who(SECOND_SHIFT_SLOT) == []
    assert not any(event[0] in ("add", "remove") for event in repo.events)


def test_picker_for_a_swap_refuses_an_assignment_on_another_place() -> None:
    client, repo = _client()
    held = repo.place(SKJENKER_SLOT, SANDER)
    response = client.get(f"/shifts/week/picker?slot_id={SECOND_SHIFT_SLOT}&replace={held}")

    assert response.status_code == 409 and "Plassen er endret" in response.json()["detail"]
