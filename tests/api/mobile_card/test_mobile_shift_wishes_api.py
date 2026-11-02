"""HTTP boundary for the wish calendar and the batch answers call."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import get_mobile_card_service, get_shifts_app_service
from app.domain.shifts.app_models import ShiftWishCalendarResponse
from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.main import create_app
from tests.api.mobile_card.test_mobile_shifts_api import AUTH, BASE, FakeMobileCardService


def _view(month: str = "2026-12") -> ShiftWishCalendarResponse:
    return ShiftWishCalendarResponse(
        month=month,
        month_label="Desember 2026",
        months=[],
        groups=[],
        note=None,
        max_shifts=None,
        shifts=[],
        mine=[],
    )


class FakeWishService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def get_wish_calendar(self, volunteer_id, month):
        self.calls.append(("calendar", volunteer_id, month))
        if month == "2030-01":
            raise ShiftNotFoundError("Finner ikke måneden.")
        return _view(month or "2026-11")

    async def set_shift_wish_answers(self, volunteer_id, items):
        self.calls.append(("answers", volunteer_id, [(i.shift_id, i.answer) for i in items]))
        if items[0].shift_id == 409:
            raise ShiftPlacementError("Planen for denne uka er publisert. Be om bytte i stedet.")
        return _view()


def _client() -> tuple[TestClient, FakeWishService]:
    app = create_app()
    service = FakeWishService()
    app.dependency_overrides[get_mobile_card_service] = lambda: FakeMobileCardService()
    app.dependency_overrides[get_shifts_app_service] = lambda: service
    return TestClient(app), service


def test_calendar_passes_the_month_and_maps_errors() -> None:
    client, service = _client()
    assert client.get(f"{BASE}/wishes/calendar", headers=AUTH).json()["month"] == "2026-11"
    assert client.get(f"{BASE}/wishes/calendar?month=2026-12", headers=AUTH).status_code == 200
    assert service.calls == [("calendar", 11, None), ("calendar", 11, "2026-12")]
    assert client.get(f"{BASE}/wishes/calendar?month=2030-01", headers=AUTH).status_code == 404
    for bad in ("2026-13", "26-12", "december"):
        assert client.get(f"{BASE}/wishes/calendar?month={bad}", headers=AUTH).status_code == 422
    assert client.get(f"{BASE}/wishes/calendar").status_code == 401
    assert client.get(f"{BASE}/wishes/calendar", headers={"Authorization": "Bearer review"}).status_code == 403


def test_batch_answers_are_validated_before_the_service_is_called() -> None:
    client, service = _client()
    ok = client.put(f"{BASE}/wishes/answers", headers=AUTH, json={"answers": [{"shift_id": 1, "answer": "vil"}, {"shift_id": 2, "answer": None}]})
    assert ok.status_code == 200 and ok.json()["month"] == "2026-12"
    assert service.calls == [("answers", 11, [(1, "vil"), (2, None)])]

    service.calls.clear()
    bad_bodies = [
        {"answers": []},
        {"answers": [{"shift_id": 1, "answer": "kanskje"}]},
        {"answers": [{"shift_id": 1, "answer": "vil"}, {"shift_id": 1, "answer": "nei"}]},
        {"answers": [{"shift_id": i, "answer": "vil"} for i in range(1, 402)]},
        {"answers": [{"shift_id": 1, "answer": "vil", "extra": 1}]},
        {},
    ]
    for body in bad_bodies:
        assert client.put(f"{BASE}/wishes/answers", headers=AUTH, json=body).status_code == 422
    assert service.calls == []
    assert client.put(f"{BASE}/wishes/answers", json={"answers": [{"shift_id": 1, "answer": "vil"}]}).status_code == 401


def test_batch_refusal_is_409_with_text() -> None:
    client, _ = _client()
    response = client.put(f"{BASE}/wishes/answers", headers=AUTH, json={"answers": [{"shift_id": 409, "answer": "vil"}]})
    assert response.status_code == 409
    assert "publisert" in response.json()["detail"]
