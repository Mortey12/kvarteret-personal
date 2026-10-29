"""scripts/shifts/copy_verv_to_qualifications.py: the copy rules (pure) and the run on SQLite."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import BigInteger, Integer, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.metadata import public_metadata
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.tables import (
    shift_group_settings,
    shift_template_slots,
    shift_templates,
    volunteer_qualifications,
)
from app.domain.volunteers.tables import volunteer_records
from scripts.shifts import copy_verv_to_qualifications as copy

NOW = 20262
PREVIOUS = 20261
OLDER = 20252
OLDEST = 20201
PARENT, STJERNE, GRONDAHLS, KJOKKEN = 1, 2, 3, 4
BARS = frozenset({STJERNE, GRONDAHLS})
KITCHENS = frozenset({KJOKKEN})
ANCESTORS = frozenset({PARENT})
SIGRID, SANDER, TORA, KARI, OLE, PIA = 10, 11, 12, 13, 14, 15


def row(volunteer_id: int, group_id: int, verv: str | None, semester: int = NOW):
    return copy.VervRow(volunteer_id=volunteer_id, group_id=group_id, semester=semester, verv_name=verv)


def run(rows, **overrides):
    options = {
        "current_semester": NOW,
        "bar_group_ids": BARS,
        "kitchen_group_ids": KITCHENS,
        "ancestor_group_ids": ANCESTORS,
    }
    return copy.map_verv(rows, **{**options, **overrides})


def keyed(result):
    return {
        (r.volunteer_id, r.qualification, r.status, r.group_id): (r.takes_trainees, r.training_target)
        for r in result.rows
    }


# ------------------------------------------------------------------ the rules


def test_skiftleder_gets_skiftleder_in_that_bar_and_skjenker() -> None:
    result = run([row(SIGRID, STJERNE, "Skiftleder")])

    assert keyed(result) == {
        (SIGRID, "skiftleder", "approved", STJERNE): (True, None),
        (SIGRID, "skjenker", "approved", STJERNE): (False, None),
    }


def test_other_verv_and_no_verv_in_a_bar_give_skjenker_in_any_semester() -> None:
    result = run(
        [row(SANDER, STJERNE, "Styremedlem", OLDEST), row(OLE, GRONDAHLS, None, OLDER)]
    )

    assert keyed(result) == {
        (SANDER, "skjenker", "approved", STJERNE): (False, None),
        (OLE, "skjenker", "approved", GRONDAHLS): (False, None),
    }


def test_skjenker_goes_on_the_bar_of_the_most_recent_semester() -> None:
    result = run([row(SANDER, STJERNE, "Skjenker", OLDER), row(SANDER, GRONDAHLS, "Skjenker", PREVIOUS)])

    assert keyed(result) == {(SANDER, "skjenker", "approved", GRONDAHLS): (False, None)}


def test_skjenker_ties_go_to_the_lowest_group_id() -> None:
    result = run([row(SANDER, GRONDAHLS, "Skjenker"), row(SANDER, STJERNE, "Skjenker")])

    assert keyed(result) == {(SANDER, "skjenker", "approved", STJERNE): (False, None)}


def test_opplaering_in_an_older_bar_semester_does_not_count_for_skjenker() -> None:
    result = run([row(TORA, STJERNE, "Opplæring", PREVIOUS)])

    assert result.rows == []


def test_skjenker_is_never_given_on_the_parent_and_the_parent_gives_no_rows() -> None:
    result = run([row(PIA, PARENT, "Medlem"), row(PIA, PARENT, "Skiftleder")])

    assert result.rows == []


def test_the_kitchen_gives_kokk_only_for_any_verv_but_opplaering() -> None:
    result = run(
        [
            row(KARI, KJOKKEN, "Kokk", OLDER),
            row(OLE, KJOKKEN, "Gruppeleder"),
            row(SIGRID, KJOKKEN, "Skiftleder"),
        ]
    )

    assert keyed(result) == {
        (KARI, "kokk", "approved", KJOKKEN): (False, None),
        (OLE, "kokk", "approved", KJOKKEN): (True, None),
        (SIGRID, "kokk", "approved", KJOKKEN): (True, None),
    }


def test_skiftleder_rows_are_per_bar() -> None:
    result = run([row(SIGRID, STJERNE, "Skiftleder"), row(SIGRID, GRONDAHLS, "Skiftleder", PREVIOUS)])

    assert {(r.qualification, r.group_id) for r in result.rows} == {
        ("skiftleder", STJERNE),
        ("skiftleder", GRONDAHLS),
        ("skjenker", STJERNE),
    }


def test_training_rows_come_from_opplaering_in_the_current_semester_only() -> None:
    result = run(
        [
            row(TORA, STJERNE, "Opplæring"),
            row(KARI, KJOKKEN, "Opplæring"),
            row(OLE, STJERNE, "Opplæring", PREVIOUS),
        ],
        training_targets={STJERNE: 2},
    )

    assert keyed(result) == {
        (TORA, "skjenker", "training", STJERNE): (False, 2),
        (KARI, "kokk", "training", KJOKKEN): (False, 3),
    }


def test_training_is_dropped_when_the_person_is_already_approved() -> None:
    result = run(
        [
            row(SANDER, GRONDAHLS, "Skjenker", OLDER),
            row(SANDER, STJERNE, "Opplæring"),
            row(KARI, KJOKKEN, "Kokk", OLDER),
            row(KARI, KJOKKEN, "Opplæring"),
        ]
    )

    assert keyed(result) == {
        (SANDER, "skjenker", "approved", GRONDAHLS): (False, None),
        (KARI, "kokk", "approved", KJOKKEN): (False, None),
    }


def test_a_kitchen_training_person_can_still_be_a_skjenker_trainee_elsewhere() -> None:
    result = run([row(KARI, KJOKKEN, "Kokk", OLDER), row(KARI, STJERNE, "Opplæring")])

    assert (KARI, "skjenker", "training", STJERNE) in keyed(result)


def test_takes_trainees_is_on_for_the_current_or_previous_semester_and_off_for_older() -> None:
    result = run(
        [
            row(SIGRID, STJERNE, "Skiftleder", NOW),
            row(SANDER, STJERNE, "Skiftleder", PREVIOUS),
            row(TORA, STJERNE, "Skiftleder", OLDER),
            row(KARI, KJOKKEN, "Kokk", PREVIOUS),
            row(OLE, KJOKKEN, "Kokk", OLDER),
        ]
    )

    by_key = keyed(result)
    assert by_key[(SIGRID, "skiftleder", "approved", STJERNE)][0] is True
    assert by_key[(SANDER, "skiftleder", "approved", STJERNE)][0] is True
    assert by_key[(TORA, "skiftleder", "approved", STJERNE)][0] is False
    assert by_key[(KARI, "kokk", "approved", KJOKKEN)][0] is True
    assert by_key[(OLE, "kokk", "approved", KJOKKEN)][0] is False
    assert by_key[(TORA, "skjenker", "approved", STJERNE)][0] is False


def test_the_latest_role_decides_takes_trainees_not_the_first() -> None:
    result = run([row(TORA, STJERNE, "Skiftleder", OLDER), row(TORA, STJERNE, "Skiftleder", NOW)])

    assert keyed(result)[(TORA, "skiftleder", "approved", STJERNE)][0] is True


def test_case_and_surrounding_spaces_are_ignored() -> None:
    result = run(
        [
            row(SIGRID, STJERNE, "  SKIFTLEDER "),
            row(TORA, STJERNE, "opplæring"),
            row(KARI, KJOKKEN, " opPLÆring "),
        ]
    )

    assert keyed(result) == {
        (SIGRID, "skiftleder", "approved", STJERNE): (True, None),
        (SIGRID, "skjenker", "approved", STJERNE): (False, None),
        (TORA, "skjenker", "training", STJERNE): (False, 3),
        (KARI, "kokk", "training", KJOKKEN): (False, 3),
    }
    assert result.unrecognised == {}


def test_every_other_verv_name_is_counted_as_unrecognised() -> None:
    result = run(
        [
            row(SANDER, STJERNE, "Skjenker"),
            row(OLE, STJERNE, " Skjenker "),
            row(KARI, KJOKKEN, "Kokk"),
            row(TORA, STJERNE, None),
            row(SIGRID, STJERNE, "Skiftleder"),
            row(PIA, PARENT, "Medlem"),
        ]
    )

    assert result.unrecognised == {"Skjenker": 2, "Kokk": 1}


def test_groups_that_are_neither_bar_kitchen_nor_ancestor_are_ignored() -> None:
    # An inactive or unrelated group is simply not in the group sets.
    result = run([row(SANDER, 99, "Skiftleder"), row(OLE, 99, "Opplæring")])

    assert result.rows == []
    assert result.unrecognised == {}


def test_the_check_list_holds_ids_for_all_four_cases() -> None:
    result = run(
        [
            row(TORA, STJERNE, "Opplæring"),  # training
            row(KARI, KJOKKEN, "Opplæring"),  # training
            row(SIGRID, STJERNE, "Skiftleder", OLDER),  # returning leader
            row(OLE, KJOKKEN, "Kokk", OLDER),  # returning kokk
            row(SANDER, STJERNE, "Skjenker"),  # in two bars now
            row(SANDER, GRONDAHLS, "Skjenker"),
            row(PIA, PARENT, "Medlem"),  # parent only, no row
            row(KARI + 100, PARENT, "Medlem"),
            row(KARI + 100, STJERNE, "Skjenker"),  # parent plus a bar: gets a row
        ]
    )

    assert result.training_people == [TORA, KARI]
    assert result.returning_leaders == [SIGRID, OLE]
    assert result.people_in_several_bars == [SANDER]
    assert result.ancestor_only_people == [PIA]


def test_a_parent_only_person_with_an_old_row_is_not_on_the_check_list() -> None:
    result = run([row(PIA, PARENT, "Medlem"), row(PIA, STJERNE, "Skjenker", OLDER)])

    assert result.ancestor_only_people == []


def test_rows_for_people_with_a_current_kitchen_role_are_not_parent_only() -> None:
    result = run([row(PIA, PARENT, "Medlem"), row(PIA, KJOKKEN, "Opplæring")])

    assert result.ancestor_only_people == []


def test_group_kinds_decide_bar_and_kitchen() -> None:
    assert copy.is_bar_group({"bartender"}) and copy.is_bar_group({"skjenker", "kokk"})
    assert copy.is_kitchen_group({"kokk", "skiftleder"})
    assert not copy.is_kitchen_group({"kokk", "bartender"})
    assert not copy.is_kitchen_group({"garderobe"})


# ------------------------------------------------------------------ the safety check


def test_apply_needs_the_exact_host_as_confirmation() -> None:
    url = "postgresql+asyncpg://u:s3cret-pw@ep-x-123.neon.example/db"
    assert copy.check_apply_target(url, "ep-x-123.neon.example") == "ep-x-123.neon.example"
    for wrong in (None, "", "yes", "ep-x-123", "localhost"):
        with pytest.raises(copy.CopyRefused, match="COPY_CONFIRM=ep-x-123.neon.example") as refused:
            copy.check_apply_target(url, wrong)
        assert "s3cret-pw" not in str(refused.value)


def test_a_url_without_a_host_is_refused() -> None:
    with pytest.raises(copy.CopyRefused, match="no host"):
        copy.check_apply_target("sqlite+aiosqlite:///x.db", "anything")


def test_main_refuses_apply_without_confirmation_and_needs_a_url(capsys) -> None:
    env = {"DATABASE_URL": "postgresql+asyncpg://u:p@db.invalid/x"}
    assert copy.main(["--apply"], env) == 2
    assert "COPY_CONFIRM=db.invalid" in capsys.readouterr().err
    assert copy.main([], {}) == 2


# ------------------------------------------------------------------ the run on SQLite

TABLES = [
    volunteer_records,
    groups,
    assignment_roles,
    role_assignments,
    shift_templates,
    shift_template_slots,
    shift_group_settings,
    volunteer_qualifications,
]
RUN_AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
async def db(monkeypatch):
    for table in TABLES:
        for column in table.columns:
            if isinstance(column.type, BigInteger):
                monkeypatch.setattr(column, "type", column.type.with_variant(Integer, "sqlite"))
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", execution_options={"schema_translate_map": {"public": None}}
    )
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: public_metadata.create_all(conn, tables=TABLES))
    async with async_sessionmaker(engine)() as session:
        yield session
    await engine.dispose()


async def _world(db) -> None:
    """Parent, two bars, a kitchen and an inactive bar; made-up people."""
    await db.execute(
        insert(groups),
        [
            {"id": PARENT, "slug": "skjenke", "name": "Skjenke", "is_active": True, "active_through_semester": 20992, "parent_group_id": None},
            {"id": STJERNE, "slug": "stjerne", "name": "Stjerne", "is_active": True, "active_through_semester": 20992, "parent_group_id": PARENT},
            {"id": GRONDAHLS, "slug": "grondahls", "name": "Grøndahls", "is_active": True, "active_through_semester": 20992, "parent_group_id": PARENT},
            {"id": KJOKKEN, "slug": "kjokken", "name": "Kjøkken", "is_active": True, "active_through_semester": 20992, "parent_group_id": PARENT},
            {"id": 5, "slug": "gammel", "name": "Gammel bar", "is_active": False, "active_through_semester": 20201, "parent_group_id": PARENT},
        ],
    )
    await db.execute(
        insert(volunteer_records),
        [
            {"id": v, "first_name": "Test", "last_name": f"Person{v}", "gender": "A", "created_at": RUN_AT}
            for v in (SIGRID, SANDER, TORA, KARI, PIA)
        ],
    )
    await db.execute(
        insert(assignment_roles),
        [
            {"id": 1, "group_id": STJERNE, "name": "Skiftleder", "penguin_points": 1},
            {"id": 2, "group_id": STJERNE, "name": "Skjenker", "penguin_points": 1},
            {"id": 3, "group_id": STJERNE, "name": "Opplæring", "penguin_points": 1},
            {"id": 4, "group_id": KJOKKEN, "name": "Kokk", "penguin_points": 1},
            {"id": 5, "group_id": PARENT, "name": "Medlem", "penguin_points": 1},
        ],
    )
    template_columns = {
        "venue": "V", "title": "Skift", "weekday": 1, "start_time": RUN_AT.time(),
        "end_time": RUN_AT.time(), "training_replaces_slot": True, "is_active": True,
    }
    await db.execute(
        insert(shift_templates),
        [{"id": g, "group_id": g, **template_columns} for g in (STJERNE, KJOKKEN, 5)],
    )
    await db.execute(
        insert(shift_template_slots),
        [
            {"template_id": STJERNE, "kind": "skjenker", "count": 1, "open_for_samf": False, "sort_order": 0},
            {"template_id": KJOKKEN, "kind": "kokk", "count": 1, "open_for_samf": False, "sort_order": 0},
            {"template_id": 5, "kind": "skjenker", "count": 1, "open_for_samf": False, "sort_order": 0},
        ],
    )
    # Grøndahls has a settings row but no templates: not a bar, so it gives no rows.
    await db.execute(
        insert(shift_group_settings),
        {"group_id": STJERNE, "app_enabled": True, "samf_enabled": False, "training_shifts_needed": 4},
    )
    role = lambda v, g, r, s: {  # noqa: E731
        "volunteer_id": v, "group_id": g, "role_id": r, "semester": s, "contract_signed": True,
    }
    await db.execute(
        insert(role_assignments),
        [
            role(SIGRID, STJERNE, 1, NOW),
            role(SANDER, STJERNE, 2, OLDER),
            role(SANDER, 5, 1, NOW),  # inactive group: ignored
            role(TORA, STJERNE, 3, NOW),
            role(KARI, KJOKKEN, 4, NOW),
            role(PIA, PARENT, 5, NOW),
        ],
    )
    await db.commit()


async def _stored(db) -> set[tuple]:
    return {
        (r.volunteer_id, r.qualification, r.status, r.group_id, r.training_target)
        for r in (await db.execute(select(volunteer_qualifications))).all()
    }


EXPECTED = {
    (SIGRID, "skiftleder", "approved", STJERNE, None),
    (SIGRID, "skjenker", "approved", STJERNE, None),
    (SANDER, "skjenker", "approved", STJERNE, None),
    (TORA, "skjenker", "training", STJERNE, 4),
    (KARI, "kokk", "approved", KJOKKEN, None),
}


async def test_a_dry_run_writes_nothing(db) -> None:
    await _world(db)

    report = await copy.run_copy(db, apply=False, now=RUN_AT)

    assert await _stored(db) == set()
    assert report.new_rows == 5 and report.existing_rows == 0
    assert report.result.ancestor_only_people == [PIA]
    assert "DRY RUN" in copy.format_report(report)


async def test_apply_writes_the_rows_and_a_second_apply_adds_nothing(db) -> None:
    await _world(db)

    first = await copy.run_copy(db, apply=True, now=RUN_AT)
    await db.commit()
    assert await _stored(db) == EXPECTED
    assert first.new_rows == 5

    stored = (await db.execute(select(volunteer_qualifications))).all()
    assert all(r.approved_by is None for r in stored)
    assert all((r.approved_at is not None) == (r.status == "approved") for r in stored)

    second = await copy.run_copy(db, apply=True, now=RUN_AT)
    await db.commit()
    assert second.new_rows == 0 and second.existing_rows == 5
    assert await _stored(db) == EXPECTED
