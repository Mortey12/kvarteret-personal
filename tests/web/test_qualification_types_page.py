"""The page where a named editor adds and changes the qualifications leaders can train for. Anyone
who manages shifts can read it; only the e-mail addresses in TRAINING_COURSE_EDITORS can change it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_settings, get_shifts_service
from app.domain.shifts.qualification_types import QualificationType
from app.main import create_app
from tests.support.helpers import make_authenticated_user, override_authenticated_user

EDITOR = "admin.user@example.test"


class Repo:
    def __init__(self) -> None:
        self.types = [
            QualificationType(1, "skjenker", "skjenker", "normal", True, None, 0),
            QualificationType(2, "skiftleder", "skiftleder", "skiftleder", True, None, 1),
        ]
        self.groups = [SimpleNamespace(group_id=7, name="Stjerne"), SimpleNamespace(group_id=8, name="Kjøkken")]

    async def list_qualification_types(self):
        return list(self.types)

    async def list_shift_groups(self):
        return self.groups

    async def get_group_name(self, group_id):
        return next((g.name for g in self.groups if g.group_id == group_id), None)

    async def add_qualification_type(self, *, key, label, style, group_id, sort_order):
        self.types.append(QualificationType(len(self.types) + 1, key, label, style, True, group_id, sort_order))

    async def update_qualification_type(self, type_id, *, label, style, active, group_id):
        for i, t in enumerate(self.types):
            if t.type_id == type_id:
                self.types[i] = QualificationType(t.type_id, t.key, label, style, active, group_id, t.sort_order)
                return True
        return False


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr("app.web.routes.shifts.qualification_types.log_admin_activity", lambda **kwargs: None)

    def make(editors: str):
        app = create_app()
        override_authenticated_user(app, make_authenticated_user())
        repo = Repo()
        service = SimpleNamespace(repository=repo)
        app.dependency_overrides[get_shifts_service] = lambda: service
        app.dependency_overrides[get_settings] = lambda: SimpleNamespace(training_course_editors=editors)
        return TestClient(app), repo

    return make


def test_everyone_who_manages_shifts_can_read_it_but_only_editors_get_the_forms(setup) -> None:
    client, _ = setup("")
    page = client.get("/shifts/qualifications").text
    assert "skjenker" in page and "skiftleder" in page
    assert 'id="qualification-types-locked"' in page and 'id="new-qualification-type"' not in page

    client, _ = setup(EDITOR)
    page = client.get("/shifts/qualifications").text
    assert 'id="new-qualification-type"' in page and 'id="qualification-types-locked"' not in page


def test_an_editor_adds_a_new_qualification_without_code(setup) -> None:
    client, repo = setup(EDITOR)

    response = client.post(
        "/shifts/qualifications",
        data={"key": "vert", "label": "vertskap", "style": "normal", "group_id": "8"},
        follow_redirects=False,
    )

    assert response.status_code == 303 and "notice" in response.headers["location"]
    added = repo.types[-1]
    assert (added.key, added.label, added.style, added.group_id, added.active) == ("vert", "vertskap", "normal", 8, True)


@pytest.mark.parametrize(
    "data, fragment",
    [
        ({"key": "Vert!", "label": "x", "style": "normal"}, "Nøkkelen må være"),
        ({"key": "vert", "label": " ", "style": "normal"}, "Navnet må fylles ut"),
        ({"key": "vert", "label": "x", "style": "rar"}, "Ukjent type"),
        ({"key": "skjenker", "label": "x", "style": "normal"}, "finnes allerede"),
        ({"key": "vert", "label": "x", "style": "normal", "group_id": "999"}, "Gruppen finnes ikke"),
    ],
)
def test_bad_input_is_refused_with_a_reason_and_nothing_is_added(setup, data, fragment) -> None:
    client, repo = setup(EDITOR)

    response = client.post("/shifts/qualifications", data=data, follow_redirects=False)

    assert response.status_code == 303
    from urllib.parse import unquote_plus

    assert fragment in unquote_plus(response.headers["location"])
    assert len(repo.types) == 2


def test_an_editor_renames_limits_or_switches_one_off(setup) -> None:
    client, repo = setup(EDITOR)

    client.post("/shifts/qualifications/1", data={"label": "skjenker (bar)", "style": "normal", "group_id": "7"}, follow_redirects=False)

    changed = repo.types[0]
    assert (changed.key, changed.label, changed.group_id, changed.active) == ("skjenker", "skjenker (bar)", 7, False)  # no "active" box sent: off


def test_nobody_else_can_change_it(setup) -> None:
    client, repo = setup("")

    add = client.post("/shifts/qualifications", data={"key": "vert", "label": "x", "style": "normal"}, follow_redirects=False)
    change = client.post("/shifts/qualifications/1", data={"label": "x", "style": "normal"}, follow_redirects=False)

    assert add.status_code == 403 and change.status_code == 403
    assert len(repo.types) == 2 and repo.types[0].label == "skjenker"
