"""Skjenkekurs, Kokkekurs and Skiftlederkurs are registered by approving a training in Skiftlista,
so an ordinary admin cannot add or remove who has completed them on the courses page. Only the
few e-mail addresses in TRAINING_COURSE_EDITORS can, and other courses are not affected."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_courses_service, get_settings
from app.domain.courses.training_courses import (
    is_training_course,
    may_edit_training_course,
)
from app.main import create_app
from tests.support.helpers import make_authenticated_user, override_authenticated_user
from tests.web.groups.test_groups_courses_web import FakeCoursesService

EDITOR = "admin.user@example.test"  # the address make_authenticated_user gives


class NamedCourse(FakeCoursesService):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.updated = False

    async def get_course_detail(self, course_id: int):
        detail = await super().get_course_detail(course_id)
        detail.name = self.name
        return detail

    async def update_course(self, course_id, *, name, description):
        self.updated = True
        return True


def client_for(course_name: str, editors: str):
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    service = NamedCourse(course_name)
    app.dependency_overrides[get_courses_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(training_course_editors=editors)
    return TestClient(app), service


def add(client):
    return client.post(
        "/courses/4/completions",
        data={"volunteer_ids": ["13"], "year": "2026", "term": "1"},
        follow_redirects=False,
    )


def test_the_helpers() -> None:
    assert is_training_course("Skjenkekurs") and is_training_course(" kokkekurs ") and is_training_course("SKIFTLEDERKURS")
    assert not is_training_course("Brannkurs") and not is_training_course(None)
    assert may_edit_training_course("A@x.no", "b@x.no, a@x.no")
    assert not may_edit_training_course("a@x.no", "") and not may_edit_training_course(None, "a@x.no")


@pytest.mark.parametrize("course", ["Skjenkekurs", "Kokkekurs", "Skiftlederkurs"])
def test_an_admin_cannot_add_remove_rename_or_delete_a_training_course_by_hand(course) -> None:
    client, service = client_for(course, editors="")

    assert add(client).status_code == 403
    assert client.post("/courses/4/completions/11?_method=DELETE", follow_redirects=False).status_code == 403
    assert client.post("/courses/4?_method=PATCH", data={"name": "Annet"}, follow_redirects=False).status_code == 403
    assert client.post("/courses/4?_method=DELETE", follow_redirects=False).status_code == 403
    assert service.created_completion is None and service.deleted_completion is None and not service.updated


def test_the_page_hides_the_forms_and_says_why() -> None:
    client, _ = client_for("Skjenkekurs", editors="")

    page = client.get("/courses/4").text

    assert 'action="/courses/4/completions"' not in page
    assert "?_method=DELETE" not in page
    assert 'id="training-course-locked"' in page
    assert "godkjenner opplæring i Skiftlista" in page


def test_a_named_editor_can_still_change_it() -> None:
    client, service = client_for("Skjenkekurs", editors=f"someone@example.test, {EDITOR}")

    assert add(client).status_code == 303
    assert service.created_completion == (4, [13], 2026, 1)
    assert 'action="/courses/4/completions"' in client.get("/courses/4").text


def test_other_courses_are_not_affected() -> None:
    client, service = client_for("Brannkurs", editors="")

    assert add(client).status_code == 303
    assert service.created_completion == (4, [13], 2026, 1)
    assert 'id="training-course-locked"' not in client.get("/courses/4").text
