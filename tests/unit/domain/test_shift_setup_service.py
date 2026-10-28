"""Closed periods, templates and the semester generator, against a real (SQLite) database.

Uses the group layout of ``tests/support/shift_groups.py``: Stjerne and Vakt nord take part
by a settings row, Kjøkken/Grøndahls/Halvtimen/Vakt by templates, Kultur does not.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import func, select

from app.domain.shifts.errors import (
    ShiftForbiddenError,
    ShiftNotFoundError,
    ShiftPlacementError,
)
from app.domain.shifts.generator import closed_period_on
from app.domain.shifts.models import ClosedPeriod
from app.domain.shifts.permissions import ALL_GROUPS, GroupScope
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.setup_service import ShiftsSetupService
from app.domain.shifts.tables import (
    shift_assignments,
    shift_slots,
    shift_templates,
    shift_week_status,
    shift_wishes,
    shifts,
)
from tests.support.shift_groups import (
    GRONDAHLS,
    HALVTIMEN,
    KJOKKEN,
    KULTUR,
    PUBKJELLER,
    SKJENKE,
    STJERNE,
    VAKT,
)

STJERNE_ONLY = GroupScope(frozenset({STJERNE}))
FORM = {
    "venue": "Stjernesalen",
    "title": "Dagskift",
    "weekday": "0",
    "start_time": "11:30",
    "end_time": "17:00",
    "training_replaces_slot": "true",
    "slot_kind_0": "skiftleder",
    "slot_count_0": "1",
    "slot_kind_1": "skjenker",
    "slot_count_1": "2",
    "slot_samf_1": "true",
}


def _service() -> ShiftsSetupService:
    return ShiftsSetupService(ShiftsRepository())


async def _sections(service: ShiftsSetupService, scope: GroupScope) -> dict:
    return {s.group_id: s for s in await service.list_closed_periods(scope)}


async def _count(db, table) -> int:
    return await db.scalar(select(func.count()).select_from(table))


async def _template(service: ShiftsSetupService, **overrides) -> int:
    await service.create_template(STJERNE, {**FORM, **overrides}, scope=STJERNE_ONLY)
    templates = await service.list_templates(STJERNE_ONLY)
    return max(t.template_id for t in templates)


# ------------------------------------------------------------------ closed periods


def _period(start: date, end: date, label: str = "Stengt") -> ClosedPeriod:
    return ClosedPeriod(1, STJERNE, start, end, label)


def test_closed_period_boundaries_are_inclusive() -> None:
    period = _period(date(2026, 12, 19), date(2027, 1, 5))
    assert closed_period_on(date(2026, 12, 18), [period]) is None
    assert closed_period_on(date(2026, 12, 19), [period]) is period
    assert closed_period_on(date(2027, 1, 5), [period]) is period
    assert closed_period_on(date(2027, 1, 6), [period]) is None
    one_day = _period(date(2026, 5, 17), date(2026, 5, 17))
    assert closed_period_on(date(2026, 5, 17), [one_day]) is one_day


def test_overlapping_periods_give_the_first_match() -> None:
    first = _period(date(2026, 12, 19), date(2026, 12, 31), "Jul")
    second = _period(date(2026, 12, 28), date(2027, 1, 5), "Nyttår")
    assert closed_period_on(date(2026, 12, 30), [first, second]).label == "Jul"
    assert closed_period_on(date(2027, 1, 2), [first, second]).label == "Nyttår"


@pytest.mark.asyncio
async def test_closed_periods_are_added_listed_and_removed(session) -> None:
    service = _service()
    message = await service.add_closed_period(
        STJERNE, starts_on=date(2026, 12, 19), ends_on=date(2027, 1, 5), label=" Juleferie ", scope=STJERNE_ONLY
    )
    assert "Juleferie" in message
    await service.add_closed_period(
        STJERNE, starts_on=date(2027, 4, 1), ends_on=date(2027, 4, 1), label="", scope=STJERNE_ONLY
    )
    periods = (await _sections(service, STJERNE_ONLY))[STJERNE].own
    assert [(p.label, p.starts_on, p.ends_on) for p in periods] == [
        ("Juleferie", date(2026, 12, 19), date(2027, 1, 5)),
        ("Stengt", date(2027, 4, 1), date(2027, 4, 1)),
    ]
    await service.remove_closed_period(periods[0].period_id, scope=STJERNE_ONLY)
    assert [p.label for p in (await _sections(service, STJERNE_ONLY))[STJERNE].own] == ["Stengt"]


@pytest.mark.asyncio
async def test_closed_period_validation_and_scope(session) -> None:
    service = _service()
    with pytest.raises(ShiftPlacementError, match="Sluttdatoen"):
        await service.add_closed_period(
            STJERNE, starts_on=date(2027, 1, 5), ends_on=date(2026, 12, 19), label="x", scope=STJERNE_ONLY
        )
    with pytest.raises(ShiftForbiddenError, match="Halvtimen"):
        await service.add_closed_period(
            HALVTIMEN, starts_on=date(2026, 12, 19), ends_on=date(2026, 12, 20), label="x", scope=STJERNE_ONLY
        )
    # A group outside Skiftlista is refused even for Admin.
    with pytest.raises(ShiftForbiddenError, match="tar ikke del"):
        await service.add_closed_period(
            KULTUR, starts_on=date(2026, 12, 19), ends_on=date(2026, 12, 20), label="x", scope=ALL_GROUPS
        )
    await service.add_closed_period(
        HALVTIMEN, starts_on=date(2026, 12, 19), ends_on=date(2026, 12, 20), label="Pause", scope=ALL_GROUPS
    )
    other = (await _sections(service, ALL_GROUPS))[HALVTIMEN].own[0]
    with pytest.raises(ShiftForbiddenError):
        await service.remove_closed_period(other.period_id, scope=STJERNE_ONLY)
    with pytest.raises(ShiftNotFoundError):
        await service.remove_closed_period(999, scope=ALL_GROUPS)
    # The leader only sees their own group's periods.
    assert set(await _sections(service, STJERNE_ONLY)) == {STJERNE}


# ----------------------------------------- listing, tree order, inherited periods

# Skjenkegruppen -> Grøndahls -> Pubkjeller is the three-level chain; Stjerne, Kjøkken and
# Halvtimen are Grøndahls' siblings. Skjenkegruppen has no settings row or templates itself.
DECEMBER = (date(2026, 12, 1), date(2026, 12, 31))
GRONDAHLS_ONLY = GroupScope(frozenset({GRONDAHLS}))
PUBKJELLER_ONLY = GroupScope(frozenset({PUBKJELLER}))


async def _close(service, group_id: int, start: date, end: date, label: str, scope=ALL_GROUPS):
    await service.add_closed_period(
        group_id, starts_on=start, ends_on=end, label=label, scope=scope
    )


@pytest.mark.asyncio
async def test_templates_and_generate_list_only_groups_with_their_own_row_or_templates(session) -> None:
    service = _service()
    await service.add_group(PUBKJELLER, scope=ALL_GROUPS)
    own = [g.name for g in await service.own_groups(ALL_GROUPS)]
    # Tree order: Skjenkegruppen's subgroups by name, Pubkjeller right under Grøndahls; Vakt last.
    assert own == ["Grøndahls", "Pubkjeller", "Halvtimen", "Kjøkken", "Stjerne", "Vakt", "Vakt nord"]
    assert "Skjenkegruppen" not in own
    # The ancestor still takes part for permissions and is listed with the wider list.
    wide = [g.name for g in await service.permitted_groups(ALL_GROUPS)]
    assert wide[0] == "Skjenkegruppen" and wide.index("Grøndahls") < wide.index("Pubkjeller")
    # A leader who only has Skjenkegruppen's scope gets its subgroups, not the parent.
    skjenke = GroupScope(frozenset({SKJENKE, STJERNE}))
    assert [g.name for g in await service.own_groups(skjenke)] == ["Stjerne"]
    with pytest.raises(ShiftPlacementError, match="allerede"):
        await service.add_group(PUBKJELLER, scope=ALL_GROUPS)


@pytest.mark.asyncio
async def test_ancestor_is_only_in_closed_periods_labelled_hele(session) -> None:
    service = _service()
    await service.add_group(PUBKJELLER, scope=ALL_GROUPS)
    sections = await service.list_closed_periods(ALL_GROUPS)
    labels = {s.group_id: s.label for s in sections}
    assert labels[SKJENKE] == "Hele Skjenkegruppen"
    assert labels[GRONDAHLS] == "Hele Grøndahls"
    assert labels[PUBKJELLER] == "Pubkjeller" and labels[STJERNE] == "Stjerne"
    order = [s.group_id for s in sections]
    assert order.index(SKJENKE) < order.index(GRONDAHLS) < order.index(PUBKJELLER)


@pytest.mark.asyncio
async def test_closed_periods_are_inherited_down_three_levels_read_only(session) -> None:
    service = _service()
    await service.add_group(PUBKJELLER, scope=ALL_GROUPS)
    await _close(service, SKJENKE, date(2026, 12, 20), date(2027, 1, 3), "Jul")
    await _close(service, GRONDAHLS, date(2026, 12, 10), date(2026, 12, 12), "Renhold")
    await _close(service, PUBKJELLER, date(2026, 12, 15), date(2026, 12, 15), "Quiz")
    sections = await _sections(service, ALL_GROUPS)
    pub = sections[PUBKJELLER]
    assert [(p.label, p.group_name) for p in pub.inherited] == [
        ("Jul", "Skjenkegruppen"),
        ("Renhold", "Grøndahls"),
    ]
    assert [p.label for p in pub.own] == ["Quiz"]
    assert [p.label for p in sections[GRONDAHLS].inherited] == ["Jul"]
    assert sections[SKJENKE].inherited == []
    assert [p.label for p in sections[STJERNE].inherited] == ["Jul"]
    assert [p.label for p in sections[HALVTIMEN].inherited] == ["Jul"]


@pytest.mark.asyncio
async def test_leader_of_one_bar_sees_but_cannot_edit_the_parents_periods(session) -> None:
    service = _service()
    await _close(service, SKJENKE, date(2026, 12, 20), date(2027, 1, 3), "Jul")
    parent_period = (await _sections(service, ALL_GROUPS))[SKJENKE].own[0]
    sections = await _sections(service, STJERNE_ONLY)
    assert set(sections) == {STJERNE}  # the parent is not listed for this leader
    assert [(p.label, p.group_name) for p in sections[STJERNE].inherited] == [("Jul", "Skjenkegruppen")]
    with pytest.raises(ShiftForbiddenError, match="Skjenkegruppen"):
        await _close(service, SKJENKE, date(2026, 12, 1), date(2026, 12, 2), "x", scope=STJERNE_ONLY)
    with pytest.raises(ShiftForbiddenError):
        await service.remove_closed_period(parent_period.period_id, scope=STJERNE_ONLY)
    # Another bar's periods are not inherited either.
    await _close(service, KJOKKEN, date(2026, 12, 1), date(2026, 12, 2), "Kjøkkenstopp")
    assert [p.label for p in (await _sections(service, STJERNE_ONLY))[STJERNE].inherited] == ["Jul"]


@pytest.mark.asyncio
async def test_leader_of_the_parent_manages_its_periods(session) -> None:
    service = _service()
    scope = GroupScope(frozenset({SKJENKE, STJERNE, KJOKKEN, GRONDAHLS, HALVTIMEN}))
    await _close(service, SKJENKE, date(2026, 12, 20), date(2027, 1, 3), "Jul", scope=scope)
    section = (await _sections(service, scope))[SKJENKE]
    assert [p.label for p in section.own] == ["Jul"]
    await service.remove_closed_period(section.own[0].period_id, scope=scope)
    assert (await _sections(service, scope))[SKJENKE].own == []


@pytest.mark.asyncio
async def test_inherited_closure_skips_generated_shifts_and_names_its_source(session) -> None:
    service = _service()
    await service.add_group(PUBKJELLER, scope=ALL_GROUPS)
    await service.create_template(PUBKJELLER, {**FORM, "weekday": "0"}, scope=PUBKJELLER_ONLY)
    await _close(service, SKJENKE, date(2026, 11, 9), date(2026, 11, 9), "Stengt for jul")
    await _close(service, GRONDAHLS, date(2026, 11, 16), date(2026, 11, 16), "Renhold")
    await _close(service, PUBKJELLER, date(2026, 11, 23), date(2026, 11, 23), "Quiz")
    await _close(service, KJOKKEN, date(2026, 11, 2), date(2026, 11, 2), "Ikke relevant")
    preview, message = await service.generate(PUBKJELLER, *NOVEMBER, scope=PUBKJELLER_ONLY)
    assert [p.day for p in preview.to_create] == [date(2026, 11, 2), date(2026, 11, 30)]
    assert [(c.shift.day, c.period.label, c.period.group_name, c.inherited) for c in preview.closed] == [
        (date(2026, 11, 9), "Stengt for jul", "Skjenkegruppen", True),
        (date(2026, 11, 16), "Renhold", "Grøndahls", True),
        (date(2026, 11, 23), "Quiz", "Pubkjeller", False),
    ]
    assert await _count(session, shifts) == 2
    assert "3 hoppet over" in message


# ---------------------------------------------------------------- adding a group


@pytest.mark.asyncio
async def test_only_admin_adds_a_group_and_it_then_takes_part(session) -> None:
    service = _service()
    assert [g.name for g in await service.list_other_groups(ALL_GROUPS)] == ["Kultur", "Pubkjeller"]
    assert await service.list_other_groups(STJERNE_ONLY) == []
    with pytest.raises(ShiftForbiddenError, match="administrator"):
        await service.add_group(KULTUR, scope=STJERNE_ONLY)
    await service.add_group(KULTUR, scope=ALL_GROUPS)
    assert KULTUR in {g.group_id for g in await service.permitted_groups(ALL_GROUPS)}
    with pytest.raises(ShiftPlacementError, match="allerede"):
        await service.add_group(KULTUR, scope=ALL_GROUPS)
    # Both switches off.
    from app.domain.shifts.service import ShiftsService

    settings = {g.group_id: g for g in await ShiftsService(ShiftsRepository()).list_group_settings(ALL_GROUPS)}
    assert (settings[KULTUR].app_enabled, settings[KULTUR].samf_enabled) == (False, False)
    # Now a leader of that group can set up templates for it.
    scope = GroupScope(frozenset({KULTUR}))
    await service.create_template(KULTUR, FORM, scope=scope)
    assert len(await service.list_templates(scope)) == 1


# ---------------------------------------------------------------------- templates


@pytest.mark.asyncio
async def test_template_create_edit_and_deactivate(session) -> None:
    service = _service()
    template_id = await _template(service)
    (template,) = await service.list_templates(STJERNE_ONLY)
    assert (template.venue, template.weekday, template.start_time, template.end_time) == (
        "Stjernesalen",
        0,
        time(11, 30),
        time(17, 0),
    )
    assert [(s.kind, s.count, s.open_for_samf) for s in template.slots] == [
        ("skiftleder", 1, False),
        ("skjenker", 2, True),
    ]
    assert template.is_active and template.training_replaces_slot

    await service.update_template(
        template_id,
        {**FORM, "title": "Kveldsskift", "weekday": "4", "end_time": "02:00", "slot_kind_1": "bartender",
         "training_replaces_slot": ""},
        scope=STJERNE_ONLY,
    )
    (edited,) = await service.list_templates(STJERNE_ONLY)
    assert (edited.title, edited.weekday, edited.end_time, edited.training_replaces_slot) == (
        "Kveldsskift", 4, time(2, 0), False
    )
    assert [s.kind for s in edited.slots] == ["skiftleder", "bartender"]

    await service.set_template_active(template_id, False, scope=STJERNE_ONLY)
    (off,) = await service.list_templates(STJERNE_ONLY)  # never deleted
    assert off.is_active is False
    await service.set_template_active(template_id, True, scope=STJERNE_ONLY)
    assert (await service.list_templates(STJERNE_ONLY))[0].is_active


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"title": " "}, "Tittel må fylles ut"),
        ({"venue": ""}, "Sted må fylles ut"),
        ({"weekday": "7"}, "Velg ukedag"),
        ({"start_time": "elleve"}, "Starttid"),
        ({"end_time": "11:30"}, "samme klokkeslett"),
        ({"slot_kind_0": "janitor"}, "Ukjent type plass"),
        ({"slot_count_1": "0"}, "Antall plasser"),
        ({"slot_count_1": "99"}, "Antall plasser"),
        ({"slot_kind_0": "", "slot_kind_1": ""}, "minst én plass"),
    ],
)
async def test_template_validation(session, overrides, message) -> None:
    with pytest.raises(ShiftPlacementError, match=message):
        await _service().create_template(STJERNE, {**FORM, **overrides}, scope=STJERNE_ONLY)
    assert await _count(session, shift_templates) == 4  # only the fixture's templates


@pytest.mark.asyncio
async def test_template_scope(session) -> None:
    service = _service()
    with pytest.raises(ShiftForbiddenError, match="Halvtimen"):
        await service.create_template(HALVTIMEN, FORM, scope=STJERNE_ONLY)
    other_id = (await session.execute(select(shift_templates.c.id).where(shift_templates.c.group_id == HALVTIMEN))).scalar_one()
    with pytest.raises(ShiftForbiddenError):
        await service.update_template(other_id, FORM, scope=STJERNE_ONLY)
    with pytest.raises(ShiftForbiddenError):
        await service.set_template_active(other_id, False, scope=STJERNE_ONLY)
    with pytest.raises(ShiftNotFoundError):
        await service.update_template(999, FORM, scope=ALL_GROUPS)
    assert {t.group_id for t in await service.list_templates(STJERNE_ONLY)} <= {STJERNE}
    assert HALVTIMEN in {t.group_id for t in await service.list_templates(ALL_GROUPS)}


# --------------------------------------------------------------------- generator

NOVEMBER = (date(2026, 11, 1), date(2026, 11, 30))  # five Mondays: 2, 9, 16, 23, 30


@pytest.mark.asyncio
async def test_preview_counts_a_month_with_five_mondays(session) -> None:
    service = _service()
    await _template(service)
    preview = await service.preview_generation(STJERNE, *NOVEMBER, scope=STJERNE_ONLY)
    assert [p.day for p in preview.to_create] == [date(2026, 11, d) for d in (2, 9, 16, 23, 30)]
    assert preview.existing == [] and preview.closed == []
    # 11:30 Oslo is 10:30 UTC in November.
    assert preview.to_create[0].starts_at == datetime(2026, 11, 2, 10, 30, tzinfo=UTC)
    # A preview writes nothing.
    assert await _count(session, shifts) == 0


@pytest.mark.asyncio
async def test_generate_copies_slots_and_is_idempotent(session) -> None:
    service = _service()
    template_id = await _template(service)
    _, message = await service.generate(STJERNE, *NOVEMBER, scope=STJERNE_ONLY)
    assert "Opprettet 5 skift" in message
    assert await _count(session, shifts) == 5
    rows = (await session.execute(select(shifts).order_by(shifts.c.starts_at))).mappings().all()
    assert {r["template_id"] for r in rows} == {template_id}
    assert {(r["group_id"], r["venue"], r["title"], r["is_closed"], r["note"]) for r in rows} == {
        (STJERNE, "Stjernesalen", "Dagskift", False, None)
    }
    assert all(r["training_replaces_slot"] for r in rows)
    slots = (await session.execute(select(shift_slots.c.shift_id, shift_slots.c.kind, shift_slots.c.count, shift_slots.c.open_for_samf, shift_slots.c.sort_order))).all()
    assert len(slots) == 10
    first = sorted((kind, count, samf, order) for sid, kind, count, samf, order in slots if sid == rows[0]["id"])
    assert first == [("skiftleder", 1, False, 0), ("skjenker", 2, True, 1)]
    assert await _count(session, shift_assignments) == 0
    assert await _count(session, shift_week_status) == 0  # statuses untouched

    again, message = await service.generate(STJERNE, *NOVEMBER, scope=STJERNE_ONLY)
    assert again.to_create == [] and len(again.existing) == 5
    assert "Opprettet 0 skift" in message
    assert await _count(session, shifts) == 5
    assert await _count(session, shift_slots) == 10


@pytest.mark.asyncio
async def test_generate_skips_closed_periods_inclusive_of_both_ends(session) -> None:
    service = _service()
    await _template(service)
    # Closes Monday 9 and Monday 16 (the period starts and ends on those days).
    await service.add_closed_period(
        STJERNE, starts_on=date(2026, 11, 9), ends_on=date(2026, 11, 16), label="Høstferie", scope=STJERNE_ONLY
    )
    preview, _ = await service.generate(STJERNE, *NOVEMBER, scope=STJERNE_ONLY)
    assert [p.day for p in preview.to_create] == [date(2026, 11, d) for d in (2, 23, 30)]
    assert [(c.shift.day, c.period.label, c.inherited) for c in preview.closed] == [
        (date(2026, 11, 9), "Høstferie", False),
        (date(2026, 11, 16), "Høstferie", False),
    ]
    assert await _count(session, shifts) == 3


@pytest.mark.asyncio
async def test_generate_never_changes_existing_shifts_and_fills_gaps(session) -> None:
    service = _service()
    template_id = await _template(service)
    await service.generate(STJERNE, date(2026, 11, 1), date(2026, 11, 15), scope=STJERNE_ONLY)
    first_id = (await session.execute(select(shifts.c.id).order_by(shifts.c.starts_at))).scalars().first()
    from sqlalchemy import update

    await session.execute(update(shifts).where(shifts.c.id == first_id).values(note="Reservert", title="Quiz"))
    # Editing the template afterwards does not touch shifts that exist.
    await service.update_template(template_id, {**FORM, "title": "Nytt navn"}, scope=STJERNE_ONLY)

    preview, _ = await service.generate(STJERNE, *NOVEMBER, scope=STJERNE_ONLY)
    assert len(preview.existing) == 2 and [p.day for p in preview.to_create] == [
        date(2026, 11, d) for d in (16, 23, 30)
    ]
    edited = (await session.execute(select(shifts).where(shifts.c.id == first_id))).mappings().one()
    assert (edited["note"], edited["title"]) == ("Reservert", "Quiz")
    assert await _count(session, shifts) == 5


@pytest.mark.asyncio
async def test_editing_a_template_time_does_not_duplicate_shifts_on_the_same_days(session) -> None:
    service = _service()
    template_id = await _template(service)
    await service.generate(STJERNE, *NOVEMBER, scope=STJERNE_ONLY)
    assert await _count(session, shifts) == 5
    # A new start time (and one that ends after midnight) must not create a second shift
    # on days that already have this template's shift.
    await service.update_template(
        template_id, {**FORM, "start_time": "23:00", "end_time": "02:00"}, scope=STJERNE_ONLY
    )
    preview, message = await service.generate(STJERNE, *NOVEMBER, scope=STJERNE_ONLY)
    assert preview.to_create == [] and len(preview.existing) == 5
    assert "Opprettet 0 skift" in message
    assert await _count(session, shifts) == 5
    # A later day the template covers but that has no shift yet is still created.
    later, _ = await service.generate(STJERNE, date(2026, 12, 1), date(2026, 12, 7), scope=STJERNE_ONLY)
    assert [p.day for p in later.to_create] == [date(2026, 12, 7)]
    assert await _count(session, shifts) == 6


@pytest.mark.asyncio
async def test_generate_handles_a_shift_past_midnight_and_skips_inactive_templates(session) -> None:
    service = _service()
    await _template(service, weekday="5", start_time="22:00", end_time="02:00", title="Nattskift")
    off = await _template(service, title="Av")
    await service.set_template_active(off, False, scope=STJERNE_ONLY)
    preview, _ = await service.generate(STJERNE, date(2026, 11, 7), date(2026, 11, 7), scope=STJERNE_ONLY)
    (planned,) = preview.to_create
    assert planned.title == "Nattskift"
    assert planned.starts_at == datetime(2026, 11, 7, 21, 0, tzinfo=UTC)
    assert planned.ends_at == datetime(2026, 11, 8, 1, 0, tzinfo=UTC)  # next day
    row = (await session.execute(select(shifts))).mappings().one()
    assert row["title"] == "Nattskift"


@pytest.mark.asyncio
async def test_generate_across_the_clock_change_keeps_oslo_time(session) -> None:
    service = _service()
    await _template(service, weekday="0", start_time="17:00", end_time="23:00")
    preview, _ = await service.generate(STJERNE, date(2027, 3, 22), date(2027, 3, 29), scope=STJERNE_ONLY)
    assert [p.starts_at.hour for p in preview.to_create] == [16, 15]  # CET, then CEST from 28 March


@pytest.mark.asyncio
async def test_generate_scope_range_and_participation(session) -> None:
    service = _service()
    await _template(service)
    with pytest.raises(ShiftForbiddenError):
        await service.generate(GRONDAHLS, *NOVEMBER, scope=STJERNE_ONLY)
    with pytest.raises(ShiftForbiddenError, match="tar ikke del"):
        await service.generate(KULTUR, *NOVEMBER, scope=ALL_GROUPS)
    with pytest.raises(ShiftPlacementError, match="Sluttdatoen"):
        await service.generate(STJERNE, date(2026, 12, 1), date(2026, 11, 1), scope=STJERNE_ONLY)
    with pytest.raises(ShiftPlacementError, match="høyst"):
        await service.generate(STJERNE, date(2026, 1, 1), date(2028, 1, 1), scope=STJERNE_ONLY)
    assert await _count(session, shifts) == 0
    # Admin can generate for a group in another tree than Skjenke.
    vakt = await service.preview_generation(VAKT, *NOVEMBER, scope=ALL_GROUPS)
    assert len(vakt.to_create) == 5


# ----------------------------------------------- removing and closing generated shifts


@pytest.mark.asyncio
async def test_remove_shift_deletes_its_places_and_wishes_but_only_when_empty(session) -> None:
    from sqlalchemy import insert

    from app.domain.volunteers.tables import volunteer_records

    service = _service()
    await _template(service)
    await service.generate(STJERNE, *NOVEMBER, scope=STJERNE_ONLY)
    first, second, *_ = (await session.execute(select(shifts.c.id).order_by(shifts.c.starts_at))).scalars().all()
    await session.execute(
        insert(volunteer_records).values(id=5, first_name="Sigrid", last_name="Test", gender="A", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    await session.execute(insert(shift_wishes).values(shift_id=first, volunteer_id=5, answer="vil"))
    slot = (await session.execute(select(shift_slots.c.id).where(shift_slots.c.shift_id == second))).scalars().first()
    await session.execute(insert(shift_assignments).values(slot_id=slot, volunteer_id=5, swap_requested=False))

    leaders = ShiftsService(ShiftsRepository())
    message = await leaders.remove_shift(shift_id=first, scope=STJERNE_ONLY)
    assert "er fjernet" in message
    assert first not in (await session.execute(select(shifts.c.id))).scalars().all()
    assert (await session.execute(select(shift_slots.c.id).where(shift_slots.c.shift_id == first))).all() == []
    assert (await session.execute(select(shift_wishes.c.shift_id))).all() == []

    with pytest.raises(ShiftPlacementError, match="Det står folk"):
        await leaders.remove_shift(shift_id=second, scope=STJERNE_ONLY)
    # With people on it, it can still be closed, and opened again.
    await leaders.set_shift_closed(shift_id=second, closed=True, scope=STJERNE_ONLY)
    assert (await session.execute(select(shifts.c.is_closed).where(shifts.c.id == second))).scalar_one()
    await leaders.set_shift_closed(shift_id=second, closed=False, scope=STJERNE_ONLY)
    assert not (await session.execute(select(shifts.c.is_closed).where(shifts.c.id == second))).scalar_one()
    with pytest.raises(ShiftForbiddenError):
        await leaders.remove_shift(shift_id=second, scope=GroupScope(frozenset({HALVTIMEN})))
    # The placed person is still on the shift.
    assert await _count(session, shift_assignments) == 1


@pytest.mark.asyncio
async def test_new_groups_start_with_course_checks_off_and_the_switch_is_saved(session) -> None:
    service = _service()
    await service.add_group(KULTUR, scope=ALL_GROUPS)
    leaders = ShiftsService(ShiftsRepository())
    settings = {g.group_id: g for g in await leaders.list_group_settings(ALL_GROUPS)}
    assert settings[KULTUR].courses_enabled is False
    await leaders.save_group_settings(
        KULTUR,
        app_enabled=True,
        samf_enabled=False,
        meeting_point="",
        courses_enabled=True,
        scope=GroupScope(frozenset({KULTUR})),
    )
    settings = {g.group_id: g for g in await leaders.list_group_settings(ALL_GROUPS)}
    assert settings[KULTUR].courses_enabled is True and settings[KULTUR].app_enabled is True


@pytest.mark.asyncio
async def test_removing_an_empty_shift_with_wishes_deletes_the_wishes_first(session) -> None:
    from sqlalchemy import insert

    from app.domain.volunteers.tables import volunteer_records

    service = _service()
    await _template(service)
    await service.generate(STJERNE, *NOVEMBER, scope=STJERNE_ONLY)
    doomed, kept, *_ = (await session.execute(select(shifts.c.id).order_by(shifts.c.starts_at))).scalars().all()
    for volunteer_id in (5, 6):
        await session.execute(
            insert(volunteer_records).values(
                id=volunteer_id, first_name="Vera", last_name="Test", gender="A",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    await session.execute(insert(shift_wishes).values(shift_id=doomed, volunteer_id=5, answer="vil"))
    await session.execute(insert(shift_wishes).values(shift_id=doomed, volunteer_id=6, answer="nei"))
    await session.execute(insert(shift_wishes).values(shift_id=kept, volunteer_id=5, answer="kan"))

    await ShiftsService(ShiftsRepository()).remove_shift(shift_id=doomed, scope=STJERNE_ONLY)

    rows = (await session.execute(select(shift_wishes.c.shift_id, shift_wishes.c.volunteer_id))).all()
    assert [tuple(row) for row in rows] == [(kept, 5)]
    assert doomed not in (await session.execute(select(shifts.c.id))).scalars().all()
    assert (await session.execute(select(shift_slots.c.id).where(shift_slots.c.shift_id == doomed))).all() == []
