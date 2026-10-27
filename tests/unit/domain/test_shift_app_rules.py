from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.shifts.app_rules import (
    cannot_attend_window_open,
    course_valid_in,
    effective_week_status,
    last_valid_semester,
    quota_for,
    required_course_keys,
    semester_label,
    semester_of,
    session_label,
    short_date,
    week_start,
    wish_window_end,
)
from app.domain.shifts.models import AssignedPerson, ShiftRecord, SlotRecord
from app.domain.shifts.planning import (
    OSLO,
    absent_to_replace,
    age_on,
    age_problem,
    build_shift_view,
    open_for_everyone,
    open_places,
    opened_by_absence,
)


def _shift(*slots: SlotRecord, replaces: bool = True) -> ShiftRecord:
    start = datetime(2026, 11, 5, 10, 30, tzinfo=UTC)
    return ShiftRecord(
        shift_id=1,
        group_id=7,
        group_name="Stjerne",
        venue="Stjernesalen",
        title="Dagskift",
        starts_at=start,
        ends_at=start.replace(hour=16),
        note=None,
        is_closed=False,
        training_replaces_slot=replaces,
        slots=list(slots),
    )


def _person(assignment_id: int, *, absent: bool = False) -> AssignedPerson:
    return AssignedPerson(assignment_id, 100 + assignment_id, "x", False, cannot_attend=absent)


def test_courses_are_valid_four_semesters() -> None:
    assert last_valid_semester(20242) == 20261
    assert last_valid_semester(20261) == 20272
    assert course_valid_in(20242, 20261)
    assert not course_valid_in(20242, 20262)
    assert course_valid_in(20262, 20262)
    assert not course_valid_in(None, 20262)
    assert semester_label(20272) == "høsten 2027"


def test_barkurs_only_for_samfunnettjeneste_in_bar_places() -> None:
    assert required_course_keys("skjenker", is_skjenke_member=True) == ["brann"]
    assert required_course_keys("skjenker", is_skjenke_member=False) == ["brann", "bar"]
    assert required_course_keys("bartender", is_skjenke_member=False) == ["brann", "bar"]
    assert required_course_keys("garderobe", is_skjenke_member=False) == ["brann"]


def test_quota_and_semester() -> None:
    assert quota_for({"Skjenker"}).period == "week"
    assert quota_for({"Pensjonist"}).period == "month"
    # Oslo time decides the semester at midnight on 1 July.
    assert semester_of(datetime(2026, 6, 30, 22, 30, tzinfo=UTC)) == 20262
    assert semester_of(datetime(2026, 6, 30, 21, 30, tzinfo=UTC)) == 20261


def test_labels() -> None:
    moment = datetime(2026, 11, 4, 17, 0, tzinfo=UTC)
    assert short_date(moment) == "4. nov"
    assert session_label(moment) == "onsdag 4. november kl. 18:00"


def test_cannot_attend_only_on_the_day() -> None:
    start = datetime(2026, 11, 5, 10, 30, tzinfo=UTC)  # 11:30 in Oslo
    end = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)
    assert not cannot_attend_window_open(start, end, datetime(2026, 11, 4, 22, 59, tzinfo=UTC))
    assert cannot_attend_window_open(start, end, datetime(2026, 11, 4, 23, 0, tzinfo=UTC))
    assert cannot_attend_window_open(start, end, datetime(2026, 11, 5, 12, 0, tzinfo=UTC))
    assert not cannot_attend_window_open(start, end, end)


def test_age() -> None:
    assert age_on(date(2006, 11, 6), date(2026, 11, 6)) == 20
    assert age_on(date(2006, 11, 7), date(2026, 11, 6)) == 19
    assert age_problem("Halvtimen", date(2007, 1, 1), date(2026, 11, 6)) == "Halvtimen krever 20 år"
    assert age_problem("Halvtimen", None, date(2026, 11, 6)) is None
    assert age_problem("Stjernesalen", date(2010, 1, 1), date(2026, 11, 6)) is None


def test_absent_person_leaves_the_place_open() -> None:
    crew = SlotRecord(11, "skjenker", 1, False, 1, [_person(1, absent=True)])
    shift = _shift(SlotRecord(10, "skiftleder", 1, False, 0, []), crew)
    assert open_places(shift, crew) == 1
    assert open_places(shift, crew, count_absent=True) == 0
    assert opened_by_absence(crew) and open_for_everyone(crew)
    assert absent_to_replace(shift, crew).assignment_id == 1
    view = build_shift_view(shift)
    crew_view = next(slot for slot in view.slots if slot.kind == "skjenker")
    assert crew_view.filled_text == "0/1" and crew_view.open_for_samf
    assert crew_view.tag == "Åpen for alle (kan ikke i dag)"


def test_absence_on_a_restricted_place_does_not_open_it_to_everyone() -> None:
    leader = SlotRecord(10, "skiftleder", 1, False, 0, [_person(1, absent=True)])
    assert opened_by_absence(leader) and not open_for_everyone(leader)


def test_free_place_is_taken_before_replacing_the_absent_person() -> None:
    crew = SlotRecord(11, "skjenker", 2, False, 1, [_person(1, absent=True)])
    shift = _shift(crew)
    assert absent_to_replace(shift, crew) is None
    assert not opened_by_absence(crew)


def test_absent_trainee_frees_the_shared_crew_place() -> None:
    crew = SlotRecord(11, "skjenker", 1, False, 1, [])
    training = SlotRecord(12, "opplaering", 1, False, 2, [_person(2, absent=True)])
    shift = _shift(crew, training)
    assert open_places(shift, crew) == 1
    assert absent_to_replace(shift, crew).assignment_id == 2


def test_week_without_a_row_depends_on_the_date() -> None:
    today = date(2026, 11, 18)  # Wednesday, week of 16 Nov
    assert effective_week_status(None, date(2026, 11, 16), today) == "published"
    assert effective_week_status(None, date(2026, 11, 9), today) == "published"
    assert effective_week_status(None, date(2026, 11, 23), today) == "open"


def test_explicit_week_status_wins() -> None:
    today = date(2026, 11, 18)
    assert effective_week_status("open", date(2026, 11, 16), today) == "open"
    assert effective_week_status("allocating", date(2026, 11, 9), today) == "allocating"
    assert effective_week_status("published", date(2026, 11, 23), today) == "published"


def test_sunday_night_shift_belongs_to_the_sunday_week() -> None:
    starts_at = datetime(2026, 11, 8, 22, 0, tzinfo=UTC)  # 23:00 Oslo, Sunday
    ends_at = datetime(2026, 11, 9, 1, 0, tzinfo=UTC)  # 02:00 Oslo, Monday
    assert week_start(starts_at.astimezone(OSLO).date()) == date(2026, 11, 2)
    assert week_start(ends_at.astimezone(OSLO).date()) == date(2026, 11, 9)
    assert week_start(datetime(2026, 11, 9, 0, 30, tzinfo=UTC).astimezone(OSLO).date()) == date(2026, 11, 9)


def test_wish_window_runs_to_the_end_of_the_semester_and_next_month() -> None:
    assert wish_window_end(date(2026, 2, 10)) == date(2026, 6, 30)
    assert wish_window_end(date(2026, 8, 10)) == date(2026, 12, 31)
    assert wish_window_end(date(2026, 6, 30)) == date(2026, 7, 31)
    assert wish_window_end(date(2026, 12, 3)) == date(2027, 1, 31)
