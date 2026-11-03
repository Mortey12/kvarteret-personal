"""A small leader week for the leader API tests: one bar, Monday and Tuesday.

Monday: Ada (skiftleder, answered "nei") and Bo on the crew slot with room for three
(so one more seat is open and shares the slot id). Tuesday: Cy "kan ikke i dag" on the
crew slot, and an empty skiftleder place.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.shifts.models import AssignedPerson, ShiftRecord, SlotRecord
from app.domain.shifts.planning import group_into_days
from app.domain.shifts.week_view import MemberInput, WeekInput, WeekView, build_week_view

BAR = 7
MONDAY = date(2026, 11, 2)


def _shift(shift_id: int, day: int, sl: list[AssignedPerson], crew: list[AssignedPerson]) -> ShiftRecord:
    # November 2026 is CET (UTC+1): 10:00 UTC is 11:00 in Oslo.
    return ShiftRecord(
        shift_id=shift_id,
        group_id=BAR,
        group_name="Stjerne",
        venue="Stjernesalen",
        title="Dagskift",
        starts_at=datetime(2026, 11, day, 10, 0, tzinfo=UTC),
        ends_at=datetime(2026, 11, day, 16, 0, tzinfo=UTC),
        note=None,
        is_closed=False,
        training_replaces_slot=False,
        slots=[
            SlotRecord(shift_id * 10 + 1, "skjenker", 3, False, 0, crew),
            SlotRecord(shift_id * 10, "skiftleder", 1, False, 1, sl),
            # An empty Opplæring place: a trainee is an extra, never a need.
            SlotRecord(shift_id * 10 + 2, "opplaering", 1, False, 2, []),
        ],
    )


def leader_week_view() -> WeekView:
    records = [
        _shift(1, 2, [AssignedPerson(101, 1, "Ada Alfa", False)], [AssignedPerson(102, 2, "Bo Beta", True)]),
        _shift(2, 3, [], [AssignedPerson(201, 3, "Cy Gamma", False, cannot_attend=True)]),
    ]
    members = [
        MemberInput(1, "Ada Alfa", ("7:skiftleder:0", "7:skjenker:0")),
        MemberInput(2, "Bo Beta", ("7:skjenker:0",)),
        MemberInput(3, "Cy Gamma", ("7:skjenker:0",)),
        MemberInput(4, "Di Delta", ("7:skjenker:0",)),
    ]
    place_state = {
        "4": {
            "21": {"ok": 0, "why": "Kun for godkjente skiftledere"},
            "22": {"ok": 1, "w": [["skift nummer 2 denne uka", "yellow"]]},
            "23": {"ok": 1, "w": [["Kun for godkjente skiftledere", "red"]], "c": 1},
        }
    }
    return build_week_view(
        WeekInput(
            bar_id=BAR,
            bar_name="Stjerne",
            week_start=MONDAY,
            days=group_into_days(records),
            members=members,
            answers={(1, 1): "nei", (1, 2): "vil"},
            shifts_per_day={(1, date(2026, 11, 2)): 1, (2, date(2026, 11, 2)): 1, (3, date(2026, 11, 3)): 1},
            month_counts={1: 3},
            began=frozenset({1, 2}),
            status="open",
            strip_statuses={MONDAY: "open", date(2026, 11, 9): "published", date(2026, 11, 16): "changing"},
            approved_skiftleder_token="7:skiftleder:0",
            place_state=place_state,
        )
    )
