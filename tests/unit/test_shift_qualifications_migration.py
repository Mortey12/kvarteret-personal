"""The verv-to-qualification mapping of migration 20261101_1200 (made-up ids)."""

from __future__ import annotations

from importlib import import_module

migration = import_module("migrations.versions.20261101_1200_shift_qualifications")
VervRow = migration.VervRow

NOW = 20262
PREVIOUS = 20261
STJERNE, GRONDAHLS, KJOKKEN = 1, 2, 3
SIGRID, SANDER, TORA, KARI, OLE = 10, 11, 12, 13, 14


def row(volunteer_id: int, group_id: int, verv: str | None, semester: int = NOW):
    return VervRow(volunteer_id=volunteer_id, group_id=group_id, semester=semester, verv_name=verv)


def keyed(result):
    return {
        (r.volunteer_id, r.qualification, r.status, r.group_id): (r.takes_trainees, r.training_target)
        for r in result.rows
    }


def test_skiftleder_gets_skiftleder_and_skjenker_with_tar_opplaering_on() -> None:
    result = migration.map_verv([row(SIGRID, STJERNE, "Skiftleder")], current_semester=NOW)

    assert keyed(result) == {
        (SIGRID, "skiftleder", "approved", STJERNE): (True, None),
        (SIGRID, "skjenker", "approved", STJERNE): (False, None),
    }


def test_kokk_gets_kokk_in_that_group_only() -> None:
    result = migration.map_verv(
        [row(KARI, KJOKKEN, "Kokk")], current_semester=NOW, kitchen_group_ids=frozenset({KJOKKEN})
    )

    assert keyed(result) == {(KARI, "kokk", "approved", KJOKKEN): (True, None)}


def test_other_verv_and_no_verv_give_skjenker() -> None:
    result = migration.map_verv(
        [row(SANDER, STJERNE, "Styremedlem"), row(OLE, STJERNE, None)], current_semester=NOW
    )

    assert keyed(result) == {
        (SANDER, "skjenker", "approved", STJERNE): (False, None),
        (OLE, "skjenker", "approved", STJERNE): (False, None),
    }


def test_opplaering_becomes_a_training_row_in_the_bar_or_the_kitchen() -> None:
    result = migration.map_verv(
        [row(TORA, STJERNE, "Opplæring"), row(KARI, KJOKKEN, "Opplæring")],
        current_semester=NOW,
        kitchen_group_ids=frozenset({KJOKKEN}),
    )

    assert keyed(result) == {
        (TORA, "skjenker", "training", STJERNE): (False, 3),
        (KARI, "kokk", "training", KJOKKEN): (False, 3),
    }
    assert result.opplaering_people == [TORA, KARI]


def test_verv_older_than_the_previous_semester_is_ignored() -> None:
    result = migration.map_verv(
        [
            row(SIGRID, STJERNE, "Skiftleder", semester=20252),
            row(SANDER, STJERNE, "Skjenker", semester=20251),
            row(TORA, STJERNE, "Skiftleder", semester=PREVIOUS),
        ],
        current_semester=NOW,
    )

    assert {r.volunteer_id for r in result.rows} == {TORA}
    # Ignored rows do not show up as unrecognised names either.
    assert result.unrecognised == {}
    assert migration.previous_semester(20261) == 20252


def test_skjenker_is_kept_once_per_person_first_group_wins_and_skiftleder_per_group() -> None:
    result = migration.map_verv(
        [
            row(SIGRID, STJERNE, "Skiftleder"),
            row(SIGRID, GRONDAHLS, "Skiftleder"),
            row(SIGRID, GRONDAHLS, "Skiftleder", semester=PREVIOUS),
        ],
        current_semester=NOW,
    )

    assert keyed(result) == {
        (SIGRID, "skiftleder", "approved", STJERNE): (True, None),
        (SIGRID, "skiftleder", "approved", GRONDAHLS): (True, None),
        (SIGRID, "skjenker", "approved", STJERNE): (False, None),
    }
    assert result.people_in_several_groups == [SIGRID]


def test_training_is_dropped_when_the_person_is_already_approved() -> None:
    result = migration.map_verv(
        [row(SANDER, STJERNE, "Skjenker"), row(SANDER, GRONDAHLS, "Opplæring")], current_semester=NOW
    )

    assert keyed(result) == {(SANDER, "skjenker", "approved", STJERNE): (False, None)}
    assert result.opplaering_people == [SANDER]


def test_unrecognised_names_are_counted_and_recognised_ones_are_not() -> None:
    result = migration.map_verv(
        [
            row(SIGRID, STJERNE, "Skiftleder"),
            row(SANDER, STJERNE, "Skjenker"),
            row(TORA, STJERNE, "Skjenker"),
            row(KARI, STJERNE, " skiftleder "),
            row(OLE, STJERNE, "Skiftleder (vikar)"),
            row(OLE, GRONDAHLS, None),
        ],
        current_semester=NOW,
    )

    assert result.unrecognised == {"Skjenker": 2, "Skiftleder (vikar)": 1}
    # Case and spaces do not matter for the recognised names.
    assert (KARI, "skiftleder", "approved", STJERNE) in keyed(result)


def test_kitchen_members_without_kokk_are_listed_for_the_check() -> None:
    result = migration.map_verv(
        [row(KARI, KJOKKEN, "Kokk"), row(OLE, KJOKKEN, "Hjelper")],
        current_semester=NOW,
        kitchen_group_ids=frozenset({KJOKKEN}),
    )

    assert result.kitchen_members_without_kokk == [OLE]


def test_a_kitchen_group_has_kokk_places_and_no_bar_places() -> None:
    assert migration.is_kitchen_group({"kokk", "garderobe"})
    assert not migration.is_kitchen_group({"kokk", "skjenker"})
    assert not migration.is_kitchen_group({"skiftleder", "bartender"})
    assert not migration.is_kitchen_group(set())
