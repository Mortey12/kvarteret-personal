"""A course marked with a qualification (courses.training_qualification) is registered by approving
a finished training in Skiftlista, so an ordinary admin cannot add or remove who has completed it
on the courses page. Only the few e-mail addresses in TRAINING_COURSE_EDITORS can, and only they
can switch the marking on or off. An unmarked course is untouched: this is an on/off setting per
course, off by default."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_courses_service, get_settings, get_shifts_service
from app.domain.shifts.qualification_types import QualificationType
from app.domain.courses.service import InvalidCourseCompletionError
from app.domain.courses.training_courses import may_edit_training_course
from app.main import create_app
from tests.support.helpers import make_authenticated_user, override_authenticated_user
from tests.web.groups.test_groups_courses_web import FakeCoursesService

EDITOR = "admin.user@example.test"  # the address make_authenticated_user gives


class MarkedCourse(FakeCoursesService):
    def __init__(self, qualification: str | None) -> None:
        super().__init__()
        self.qualification = qualification
        self.updated = False
        self.taken_by_other = False

    async def get_training_qualification(self, course_id: int):
        return self.qualification

    async def set_training_qualification(self, course_id, qualification, *, allowed):
        if qualification and qualification not in allowed:
            raise InvalidCourseCompletionError("Ukjent kvalifikasjon.")
        if qualification and self.taken_by_other:
            raise InvalidCourseCompletionError("Kvalifikasjonen brukes allerede av kurset «Annet».")
        self.qualification = qualification

    async def update_course(self, course_id, *, name, description):
        self.updated = True
        return True


def client_for(qualification: str | None, editors: str):
    app = create_app()
    override_authenticated_user(app, make_authenticated_user())
    service = MarkedCourse(qualification)
    app.dependency_overrides[get_courses_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(training_course_editors=editors)
    types = [
        QualificationType(1, "skjenker", "skjenker", "normal", True, None, 0),
        QualificationType(2, "kokk", "kokk", "normal", True, None, 1),
        QualificationType(3, "skiftleder", "skiftleder", "skiftleder", True, None, 2),
        QualificationType(4, "vert", "vertskap", "normal", True, None, 3),
        QualificationType(5, "gammel", "gammel", "normal", False, None, 4),
    ]

    async def list_qualification_types():
        return types

    shifts = SimpleNamespace(repository=SimpleNamespace(list_qualification_types=list_qualification_types))
    app.dependency_overrides[get_shifts_service] = lambda: shifts
    return TestClient(app), service


def add(client):
    return client.post(
        "/courses/4/completions",
        data={"volunteer_ids": ["13"], "year": "2026", "term": "1"},
        follow_redirects=False,
    )


def test_editor_check() -> None:
    assert may_edit_training_course("A@x.no", "b@x.no, a@x.no")
    assert not may_edit_training_course("a@x.no", "") and not may_edit_training_course(None, "a@x.no")


@pytest.mark.parametrize("qualification", ["skjenker", "kokk", "skiftleder"])
def test_an_admin_cannot_add_remove_rename_or_delete_a_marked_course_by_hand(qualification) -> None:
    client, service = client_for(qualification, editors="")

    assert add(client).status_code == 403
    assert client.post("/courses/4/completions/11?_method=DELETE", follow_redirects=False).status_code == 403
    assert client.post("/courses/4?_method=PATCH", data={"name": "Annet"}, follow_redirects=False).status_code == 403
    assert client.post("/courses/4?_method=DELETE", follow_redirects=False).status_code == 403
    assert service.created_completion is None and service.deleted_completion is None and not service.updated


def test_the_page_hides_the_forms_and_says_why_but_has_no_settings_for_them() -> None:
    client, _ = client_for("skjenker", editors="")

    page = client.get("/courses/4").text

    assert 'action="/courses/4/completions"' not in page
    assert "?_method=DELETE" not in page
    assert 'id="training-course-locked"' in page and "godkjenner opplæring i Skiftlista" in page
    assert 'id="training-settings"' not in page


def test_a_named_editor_sees_the_settings_and_can_still_change_the_course() -> None:
    client, service = client_for("skjenker", editors=f"someone@example.test, {EDITOR}")

    assert add(client).status_code == 303
    assert service.created_completion == (4, [13], 2026, 1)
    page = client.get("/courses/4").text
    assert 'action="/courses/4/completions"' in page
    assert 'id="training-settings"' in page and "Skiftlista: opplæring" in page
    assert 'id="training-course-locked"' not in page


def test_an_unmarked_course_is_untouched_and_shows_nothing_extra_to_ordinary_admins() -> None:
    client, service = client_for(None, editors="")

    assert add(client).status_code == 303
    assert service.created_completion == (4, [13], 2026, 1)
    page = client.get("/courses/4").text
    assert 'id="training-course-locked"' not in page and 'id="training-settings"' not in page


def test_only_a_named_editor_can_switch_it_on_or_off() -> None:
    client, service = client_for(None, editors="")
    denied = client.post("/courses/4/training-settings", data={"enabled": "on", "qualification": "kokk"}, follow_redirects=False)
    assert denied.status_code == 403 and service.qualification is None

    client, service = client_for(None, editors=EDITOR)
    turned_on = client.post("/courses/4/training-settings", data={"enabled": "on", "qualification": "kokk"}, follow_redirects=False)
    assert turned_on.status_code == 303 and service.qualification == "kokk"
    turned_off = client.post("/courses/4/training-settings", data={"qualification": "kokk"}, follow_redirects=False)
    assert turned_off.status_code == 303 and service.qualification is None


def test_switching_on_needs_a_qualification_and_one_course_per_qualification() -> None:
    client, service = client_for(None, editors=EDITOR)
    assert client.post("/courses/4/training-settings", data={"enabled": "on", "qualification": ""}, follow_redirects=False).status_code == 400

    service.taken_by_other = True
    taken = client.post("/courses/4/training-settings", data={"enabled": "on", "qualification": "skjenker"}, follow_redirects=False)
    assert taken.status_code == 409 and "brukes allerede" in taken.json()["detail"]
    assert service.qualification is None


def test_the_settings_choices_come_from_the_qualification_types_in_the_data() -> None:
    client, service = client_for(None, editors=EDITOR)

    page = client.get("/courses/4").text

    assert 'value="vert"' in page and ">vertskap<" in page  # a type someone added without code
    assert 'value="gammel"' not in page  # switched off
    on = client.post("/courses/4/training-settings", data={"enabled": "on", "qualification": "vert"}, follow_redirects=False)
    assert on.status_code == 303 and service.qualification == "vert"
    unknown = client.post("/courses/4/training-settings", data={"enabled": "on", "qualification": "finnesikke"}, follow_redirects=False)
    assert unknown.status_code == 409
