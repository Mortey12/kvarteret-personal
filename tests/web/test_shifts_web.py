from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.auth.roles import UserRole
from app.dependencies import get_shifts_service
from app.domain.shifts.models import (
    AssignedPerson,
    ShiftGroupOption,
    ShiftRecord,
    SlotRecord,
    WeekPlan,
)
from app.domain.shifts.planning import format_week, group_into_days, monday_of
from app.main import create_app
from tests.support.helpers import make_authenticated_user, override_authenticated_user


class FakeShiftsService:
    def __init__(self) -> None:
        self.calls: list[tuple[date, int | None]] = []

    async def get_week_plan(self, week: date, group_id: int | None = None) -> WeekPlan:
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
                    [AssignedPerson(1, 1, "Birk Lie", swap_requested=True)],
                ),
                SlotRecord(2, "skjenker", 1, True, 1, []),
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
        )


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
    assert service.calls == [(date(2026, 11, 4), 7)]
    assert "Skiftplan" in response.text
    assert "Uke 45 · 2.–8. november" in response.text
    assert "Mandag 2. november" in response.text
    assert "Stjernesalen og kjøkken" in response.text
    assert "Birk Lie" in response.text
    assert "ønsker bytte" in response.text
    assert "Brukt til opplæring" in response.text
    assert "Reservert bord 22 fra 18" in response.text
    assert 'href="/shifts?week=2026-11-09&amp;group=7"' in response.text


def test_shift_plan_ignores_bad_query_values() -> None:
    client, service = _client(UserRole.GROUP_ADMIN)
    response = client.get("/shifts?week=not-a-date&group=abc")

    assert response.status_code == 200
    assert service.calls[0][1] is None


def test_shift_plan_is_for_managers_only() -> None:
    client, _ = _client(UserRole.VOLUNTEER)
    response = client.get("/shifts")

    assert response.status_code == 403
