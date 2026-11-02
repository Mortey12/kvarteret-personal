# ruff: noqa: F811  (the world fixture is imported from test_shift_app_service)
"""The wish calendar and the batch call, against a real (SQLite) database.

Uses the world of test_shift_app_service: clock Monday 2 November 2026,
09:00 in Oslo. The current week is published, later weeks are open.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest
from sqlalchemy import select

from app.domain.shifts.app_models import WishAnswerItem, WishAnswersRequest
from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.tables import shift_assignments, shift_week_status, shift_wishes
from app.domain.shifts.wish_calendar import month_options, parse_month, refusal_reason, refusal_text
from tests.unit.domain.test_shift_app_service import (  # noqa: F401  (world is a fixture)
    HALVTIMEN,
    KULTUR,
    OLA,
    SANDER,
    SIGRID,
    STJERNE,
    World,
    add_stjerne_shift,
    oslo,
    world,
)


def items(*pairs: tuple[int, str | None]) -> list[WishAnswerItem]:
    return [WishAnswerItem(shift_id=shift_id, answer=answer) for shift_id, answer in pairs]


async def stored(world: World, volunteer_id: int = SANDER) -> dict[int, str]:
    rows = await world.session.execute(
        select(shift_wishes.c.shift_id, shift_wishes.c.answer).where(shift_wishes.c.volunteer_id == volunteer_id)
    )
    return {shift_id: answer for shift_id, answer in rows.all()}


# ---------------------------------------------------------------- pure


def test_months_run_from_the_start_of_the_semester_to_the_end_of_the_wish_window() -> None:
    def keys(today: date) -> list[str]:
        return [m.isoformat()[:7] for m in month_options(today)]

    # Start of the autumn semester.
    assert keys(date(2026, 7, 1)) == ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12"]
    # Mid semester: back to July, forward to December.
    assert keys(date(2026, 11, 2)) == ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12"]
    assert keys(date(2026, 3, 10)) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    # December: the window reaches into January, the semester start stays July.
    assert keys(date(2026, 12, 15)) == [
        "2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12", "2027-01",
    ]  # fmt: skip
    # January: a new semester, so December is no longer listed.
    assert keys(date(2027, 1, 5)) == ["2027-01", "2027-02", "2027-03", "2027-04", "2027-05", "2027-06"]


def test_parse_month() -> None:
    assert parse_month("2026-12") == date(2026, 12, 1)
    assert parse_month("2026-13") is None and parse_month("26-12") is None and parse_month("") is None


def test_refusal_reasons_and_text() -> None:
    assert refusal_reason(visible=True, started=False, status="open") is None
    assert refusal_reason(visible=True, started=False, status="allocating") is None
    assert refusal_reason(visible=False, started=False, status="open") == "Finner ikke skiftet."
    assert refusal_reason(visible=True, started=True, status="open") == "Skiftet har allerede vært."
    assert "publisert" in (refusal_reason(visible=True, started=False, status="published") or "")
    assert refusal_text(["Første.", "Andre."], 5) == "Første. (2 av 5 skift kunne ikke endres. Ingenting er lagret.)"


def test_request_refuses_duplicates_and_bad_sizes() -> None:
    with pytest.raises(ValueError):
        WishAnswersRequest(answers=items((1, "vil"), (1, "nei")))
    with pytest.raises(ValueError):
        WishAnswersRequest(answers=[])
    with pytest.raises(ValueError):
        WishAnswersRequest(answers=items(*[(i, "vil") for i in range(1, 402)]))
    assert len(WishAnswersRequest(answers=items(*[(i, "vil") for i in range(1, 401)])).answers) == 400


# ---------------------------------------------------------------- month view


async def test_month_view_lists_shifts_groups_and_months(world: World) -> None:
    view = await world.service.get_wish_calendar(SANDER, "2026-11")
    assert view.month == "2026-11" and view.month_label == "November 2026"
    assert [m.month for m in view.months] == [
        "2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12",
    ]  # fmt: skip
    assert view.note is None and view.max_shifts is None
    # Sander is a member of Stjerne only; Halvtimen's shift is not his.
    assert [s.shift_id for s in view.shifts] == [world.shift_ids["tue"], world.shift_ids["thu"]]
    tue = view.shifts[0]
    assert (tue.day, tue.time_label, tue.title, tue.group_name) == (date(2026, 11, 3), "11:30–17:00", "Dagskift", "Stjerne")
    # The current week is published: stored answer shown, but no longer open for wishes.
    assert not tue.accepts_wishes and not tue.past and tue.answer is None
    [group] = view.groups
    assert (group.group_id, group.has_answered, group.deadline, group.days_left) == (STJERNE, False, None, None)

    december = await world.service.get_wish_calendar(SANDER, "2026-12")
    [dec] = december.shifts
    assert dec.shift_id == world.shift_ids["dec"] and dec.accepts_wishes


async def test_month_defaults_to_this_month_and_refuses_months_out_of_range(world: World) -> None:
    assert (await world.service.get_wish_calendar(SANDER)).month == "2026-11"
    for month in ("2026-06", "2027-01", "nonsense"):
        with pytest.raises(ShiftNotFoundError):
            await world.service.get_wish_calendar(SANDER, month)


async def test_past_month_of_this_semester_is_read_only_with_answers_and_own_assignments(world: World) -> None:
    started, slot = await add_stjerne_shift(world, oslo(date(2026, 10, 6), 11), oslo(date(2026, 10, 6), 17))
    await world.session.execute(
        shift_wishes.insert().values(volunteer_id=SANDER, shift_id=started, answer="vil")
    )
    await world.session.execute(
        shift_assignments.insert().values(slot_id=slot, volunteer_id=SANDER, swap_requested=False)
    )
    view = await world.service.get_wish_calendar(SANDER, "2026-10")
    assert view.month == "2026-10" and view.month_label == "Oktober 2026"
    [shift] = view.shifts
    assert (shift.shift_id, shift.past, shift.accepts_wishes, shift.answer) == (started, True, False, "vil")
    assert [(m.shift_id, m.kind, m.day) for m in view.mine] == [(started, "skjenker", date(2026, 10, 6))]
    assert view.groups[0].has_answered
    # Someone who was not on the shift sees the shift but no assignment.
    assert (await world.service.get_wish_calendar(SIGRID, "2026-10")).mine == []


async def test_started_shifts_are_past_and_do_not_take_wishes(world: World) -> None:
    world.clock.now = oslo(date(2026, 11, 3), 12)
    view = await world.service.get_wish_calendar(SANDER, "2026-11")
    tue = next(s for s in view.shifts if s.shift_id == world.shift_ids["tue"])
    assert tue.past and not tue.accepts_wishes


async def test_has_answered_counts_any_answer_on_a_shift_of_the_group(world: World) -> None:
    await world.service.set_shift_wish_answers(SANDER, items((world.shift_ids["dec"], "nei")))
    assert (await world.service.get_wish_calendar(SANDER, "2026-12")).groups[0].has_answered
    # Another month stays unanswered.
    assert not (await world.service.get_wish_calendar(SANDER, "2026-11")).groups[0].has_answered
    # Another person is not affected.
    assert not (await world.service.get_wish_calendar(SIGRID, "2026-12")).groups[0].has_answered


async def test_drafts_never_show_and_mine_holds_published_shifts_only(world: World) -> None:
    await world.place("tue", "skjenker", SANDER)  # week 45 is published
    dec_assignment = await world.place("dec", "skjenker", SANDER)  # week 49 is open: a draft
    assert dec_assignment
    view = await world.service.get_wish_calendar(SANDER, "2026-11")
    assert [(m.shift_id, m.kind, m.day) for m in view.mine] == [(world.shift_ids["tue"], "skjenker", date(2026, 11, 3))]
    december = await world.service.get_wish_calendar(SANDER, "2026-12")
    assert december.mine == []
    # Publish the December week: the shift now counts as mine and stops taking wishes.
    await world.session.execute(
        shift_week_status.insert().values(group_id=STJERNE, week_start=date(2026, 11, 30), status="published")
    )
    december = await world.service.get_wish_calendar(SANDER, "2026-12")
    assert [m.shift_id for m in december.mine] == [world.shift_ids["dec"]]
    assert not december.shifts[0].accepts_wishes


async def test_published_week_shows_the_stored_answer_dimmed(world: World) -> None:
    await world.service.set_shift_wish_answers(SANDER, items((world.shift_ids["dec"], "vil")))
    await world.session.execute(
        shift_week_status.insert().values(group_id=STJERNE, week_start=date(2026, 11, 30), status="published")
    )
    [dec] = (await world.service.get_wish_calendar(SANDER, "2026-12")).shifts
    assert dec.answer == "vil" and not dec.accepts_wishes


async def test_calendar_is_for_skjenke_members_only(world: World) -> None:
    from app.domain.shifts.errors import ShiftAccessError

    with pytest.raises(ShiftAccessError):
        await world.service.get_wish_calendar(OLA, "2026-11")


# ---------------------------------------------------------------- batch


async def open_shifts(world: World) -> list[int]:
    a, _ = await add_stjerne_shift(world, oslo(date(2026, 11, 10), 11), oslo(date(2026, 11, 10), 17))
    b, _ = await add_stjerne_shift(world, oslo(date(2026, 11, 11), 11), oslo(date(2026, 11, 11), 17))
    return [a, b, world.shift_ids["dec"]]


async def test_batch_sets_clears_and_returns_the_month_of_the_first_item(world: World) -> None:
    a, b, dec = await open_shifts(world)
    view = await world.service.set_shift_wish_answers(SANDER, items((a, "vil"), (b, "kan"), (dec, "nei")))
    assert view.month == "2026-11"
    assert {s.shift_id: s.answer for s in view.shifts if s.shift_id in (a, b)} == {a: "vil", b: "kan"}
    assert await stored(world) == {a: "vil", b: "kan", dec: "nei"}

    # Replace one, clear one, leave the rest alone; the first item picks the month.
    view = await world.service.set_shift_wish_answers(SANDER, items((dec, None), (a, "nei")))
    assert view.month == "2026-12"
    assert await stored(world) == {a: "nei", b: "kan"}


async def test_batch_is_idempotent(world: World) -> None:
    a, b, _ = await open_shifts(world)
    first = await world.service.set_shift_wish_answers(SANDER, items((a, "vil"), (b, None)))
    second = await world.service.set_shift_wish_answers(SANDER, items((a, "vil"), (b, None)))
    assert first == second
    assert await stored(world) == {a: "vil"}


async def test_batch_with_one_refused_item_writes_nothing(world: World) -> None:
    a, b, _ = await open_shifts(world)
    await world.service.set_shift_wish_answers(SANDER, items((a, "kan")))
    with pytest.raises(ShiftPlacementError, match=r"publisert.*1 av 3 skift"):
        await world.service.set_shift_wish_answers(
            SANDER, items((a, "vil"), (world.shift_ids["tue"], "vil"), (b, "vil"))
        )
    assert await stored(world) == {a: "kan"}  # not even the valid items went through


async def test_batch_refuses_started_unknown_and_foreign_shifts_with_a_count(world: World) -> None:
    a, _, _ = await open_shifts(world)
    with pytest.raises(ShiftPlacementError, match=r"Finner ikke skiftet.*2 av 3 skift"):
        await world.service.set_shift_wish_answers(
            SANDER, items((a, "vil"), (world.shift_ids["fri"], "vil"), (99999, "vil"))
        )
    world.clock.now = oslo(date(2026, 11, 10), 12)
    with pytest.raises(ShiftPlacementError, match="allerede vært"):
        await world.service.set_shift_wish_answers(SANDER, items((a, "vil")))
    assert await stored(world) == {}


async def test_batch_only_touches_the_callers_own_rows(world: World) -> None:
    a, b, _ = await open_shifts(world)
    await world.service.set_shift_wish_answers(SIGRID, items((a, "vil")))
    await world.service.set_shift_wish_answers(SANDER, items((a, "nei"), (b, "kan")))
    await world.service.set_shift_wish_answers(SANDER, items((a, None)))
    assert await stored(world, SIGRID) == {a: "vil"}


async def test_old_allocating_weeks_still_take_batch_answers(world: World) -> None:
    a, _, _ = await open_shifts(world)
    await world.session.execute(
        shift_week_status.insert().values(group_id=STJERNE, week_start=date(2026, 11, 9), status="allocating")
    )
    await world.service.set_shift_wish_answers(SANDER, items((a, "vil")))
    assert await stored(world) == {a: "vil"}


async def test_wish_events_are_logged_without_the_answers(world: World, caplog) -> None:
    a, b, _ = await open_shifts(world)
    with caplog.at_level(logging.INFO, logger="app.audit"):
        await world.service.set_shift_wish_answers(SANDER, items((a, "vil"), (b, None)))
        await world.service.set_shift_wish(SANDER, a, "kan")
    events = [r for r in caplog.records if getattr(r, "event", "") == "shift.app.wishes"]
    assert [(r.event_data["volunteer_id"], r.event_data["count"]) for r in events] == [(SANDER, 2), (SANDER, 1)]
    assert all("answer" not in r.event_data for r in events)


async def test_only_listed_groups_matter(world: World) -> None:
    # Halvtimen is not Sander's group: its shift is refused even though it is in an open week.
    assert HALVTIMEN != KULTUR
    with pytest.raises(ShiftPlacementError):
        await world.service.set_shift_wish_answers(SANDER, items((world.shift_ids["fri"], "vil")))


# ---------------------------------------------------------------- "Har svart N av M"


def test_answered_summary_counts_members_and_sorts_missing_names() -> None:
    from app.domain.shifts.wish_calendar import answered_summary, week_month

    view = answered_summary(
        group_id=1,
        group_name="Stjerne",
        month=date(2026, 12, 1),
        members={1: "Øyvind Ø", 2: "anna A", 3: "Berit B"},
        answered={3},
    )
    assert (view.answered, view.total, view.month_label) == (1, 3, "Desember 2026")
    assert view.missing == ["anna A", "Øyvind Ø"]
    assert answered_summary(group_id=1, group_name="X", month=date(2026, 12, 1), members={}, answered=set()).total == 0
    # A week counts for the month of its Thursday.
    assert week_month(date(2026, 11, 30)) == date(2026, 12, 1)
    assert week_month(date(2026, 10, 26)) == date(2026, 10, 1)


async def test_board_counts_who_has_answered_from_shift_wishes_only(world: World) -> None:
    from app.domain.shifts.permissions import ALL_GROUPS

    board = world.service.shifts
    plan = await board.get_week_plan(date(2026, 11, 30), scope=ALL_GROUPS)
    stjerne = next(a for a in plan.answered if a.group_id == STJERNE)
    assert (stjerne.answered, stjerne.total, stjerne.month_label) == (0, 3, "Desember 2026")
    assert stjerne.missing == ["Sander Skjenker", "Sigrid Skiftleder", "Tora Trainee"]

    await world.service.set_shift_wish_answers(SANDER, items((world.shift_ids["dec"], "nei")))  # any answer counts
    plan = await board.get_week_plan(date(2026, 11, 30), scope=ALL_GROUPS)
    stjerne = next(a for a in plan.answered if a.group_id == STJERNE)
    assert (stjerne.answered, stjerne.total) == (1, 3)
    assert stjerne.missing == ["Sigrid Skiftleder", "Tora Trainee"]

    # Another month is counted on its own, and clearing the answer takes it back.
    november = await board.get_week_plan(date(2026, 11, 2), scope=ALL_GROUPS)
    assert next(a for a in november.answered if a.group_id == STJERNE).answered == 0
    await world.service.set_shift_wish_answers(SANDER, items((world.shift_ids["dec"], None)))
    plan = await board.get_week_plan(date(2026, 11, 30), scope=ALL_GROUPS)
    assert next(a for a in plan.answered if a.group_id == STJERNE).answered == 0


async def test_board_answered_counts_stay_inside_the_leaders_scope(world: World) -> None:
    from app.domain.shifts.permissions import NO_GROUPS, GroupScope

    board = world.service.shifts
    only_halvtimen = await board.get_week_plan(date(2026, 11, 30), scope=GroupScope(frozenset({HALVTIMEN})))
    assert [a.group_id for a in only_halvtimen.answered] == [HALVTIMEN]
    assert only_halvtimen.answered[0].total == 0 and only_halvtimen.answered[0].missing == []
    assert (await board.get_week_plan(date(2026, 11, 30), scope=NO_GROUPS)).answered == []


# ---------------------------------------------------------------- the unpublished-week banner


async def test_banners_list_every_bar_in_scope_with_unpublished_weeks_within_ten_days(world: World) -> None:
    from sqlalchemy import insert

    from app.domain.shifts.permissions import ALL_GROUPS, GroupScope
    from app.domain.shifts.tables import shift_slots, shifts

    # Clock is Monday 2 Nov: the week of 9 Nov is 7 days ahead, the week of 16 Nov is 14.
    await add_stjerne_shift(world, oslo(date(2026, 11, 10), 11), oslo(date(2026, 11, 10), 17))
    await add_stjerne_shift(world, oslo(date(2026, 11, 17), 11), oslo(date(2026, 11, 17), 17))
    halv = (
        await world.session.execute(
            insert(shifts)
            .values(
                group_id=HALVTIMEN, venue="Halvtimen", title="Skift", starts_at=oslo(date(2026, 11, 11), 20),
                ends_at=oslo(date(2026, 11, 12), 2), is_closed=False, training_replaces_slot=True,
            )
            .returning(shifts.c.id)
        )
    ).scalar_one()
    await world.session.execute(
        insert(shift_slots).values(shift_id=halv, kind="bartender", count=2, open_for_samf=False, sort_order=0)
    )

    banners = (await world.service.shifts.get_week_plan(date(2026, 11, 2), scope=ALL_GROUPS)).banners
    assert {(b.group_id, b.week_start, b.empty_places, b.urgent) for b in banners} == {
        (STJERNE, date(2026, 11, 9), 1, False),
        (HALVTIMEN, date(2026, 11, 9), 2, False),
    }
    assert all("blir åpne for alle mandag 9. november" in b.text for b in banners)

    # A leader of one bar only sees that bar's banner.
    own = (await world.service.shifts.get_week_plan(date(2026, 11, 2), scope=GroupScope(frozenset({HALVTIMEN})))).banners
    assert [b.group_id for b in own] == [HALVTIMEN]

    # Three days before the Monday the banner turns strong.
    world.clock.now = oslo(date(2026, 11, 6), 9)
    banners = (await world.service.shifts.get_week_plan(date(2026, 11, 2), scope=ALL_GROUPS)).banners
    assert banners and all(b.urgent for b in banners if b.week_start == date(2026, 11, 9))
