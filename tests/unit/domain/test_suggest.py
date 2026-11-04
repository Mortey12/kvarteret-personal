"""The suggestion engine (plan B, plain Python): hard rules, fairness, the swap steps, determinism."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.shifts import suggest as engine
from app.domain.shifts.suggest import PersonSpec, PlaceSpec, SuggestInput, suggest

MONDAY = datetime(2026, 11, 2, 16, tzinfo=UTC)
X, Y, Z, W = 1, 2, 3, 4


def place(
    slot: int,
    day: int = 0,
    *,
    seat: int = 0,
    kind: str = "skjenker",
    hours: int = 5,
    start: int = 0,
):
    begin = MONDAY + timedelta(days=day, hours=start)
    return PlaceSpec(
        slot,
        seat,
        shift_id=slot * 10,
        kind=kind,
        starts_at=begin,
        ends_at=begin + timedelta(hours=hours),
    )


def person(volunteer_id: int, **kwargs):
    return PersonSpec(
        volunteer_id,
        f"Person{volunteer_id} Etternavn",
        f"Person{volunteer_id}",
        **kwargs,
    )


def build(places, people, answers, allowed=None):
    """``answers``: {(slot, person): 'vil'|'kan'|'nei'}; ``allowed`` defaults to everybody everywhere."""
    by_slot = {p.slot_id: p for p in places}
    return SuggestInput(
        places=tuple(places),
        people=tuple(people),
        answers={
            (by_slot[slot].shift_id, who): answer
            for (slot, who), answer in answers.items()
        },
        allowed=frozenset(
            allowed
            if allowed is not None
            else {(p.slot_id, q.volunteer_id) for p in places for q in people}
        ),
    )


def who(output, slot: int) -> list[int]:
    return [pick.volunteer_id for pick in output.picks if pick.slot_id == slot]


# ---- hard rules ----------------------------------------------------------------------------------


def test_only_people_who_offered_are_suggested_never_kan_ikke_never_unanswered() -> (
    None
):
    p = place(1)
    out = suggest(
        build([p], [person(X), person(Y), person(Z)], {(1, X): "nei", (1, Y): "kan"})
    )

    assert who(out, 1) == [Y]  # X said no, Z did not answer


def test_a_place_nobody_offered_for_stays_empty() -> None:
    p = place(1)
    out = suggest(build([p], [person(X)], {(1, X): "nei"}))

    assert out.picks == [] and out.unfilled == [p]


def test_the_servers_rules_decide_who_may_take_a_place() -> None:
    p = place(1, kind="skiftleder")
    allowed = {(1, Y)}  # X lacks the approval
    out = suggest(
        build([p], [person(X), person(Y)], {(1, X): "vil", (1, Y): "kan"}, allowed)
    )

    assert who(out, 1) == [Y]


def test_a_shift_that_overlaps_one_the_person_already_has_is_never_suggested() -> None:
    p = place(1, start=1)
    busy = ((MONDAY, MONDAY + timedelta(hours=5)),)  # a placed shift that overlaps
    out = suggest(
        build([p], [person(X, busy=busy), person(Y)], {(1, X): "vil", (1, Y): "kan"})
    )

    assert who(out, 1) == [Y]


def test_one_person_never_gets_two_overlapping_places() -> None:
    a, b = place(1), place(2, start=2)  # overlap
    out = suggest(
        build(
            [a, b],
            [person(X), person(Y)],
            {(1, X): "vil", (2, X): "vil", (1, Y): "kan", (2, Y): "kan"},
        )
    )

    assert sorted(pick.volunteer_id for pick in out.picks) == [X, Y]


def test_the_weekly_maximum_is_respected() -> None:
    places = [place(1, 0), place(2, 1)]
    out = suggest(
        build(places, [person(X, max_week_shifts=1)], {(1, X): "vil", (2, X): "vil"}),
    )

    assert len(out.picks) == 1 and len(out.unfilled) == 1
    out = suggest(
        build(
            places,
            [person(X, max_week_shifts=1, week_shifts=1)],
            {(1, X): "vil", (2, X): "vil"},
        )
    )
    assert out.picks == []  # the one shift they already have uses it up


def test_a_slot_with_two_seats_gets_two_different_people() -> None:
    seats = [place(1, seat=0), place(1, seat=1)]
    out = suggest(build(seats, [person(X), person(Y)], {(1, X): "vil", (1, Y): "vil"}))

    assert sorted(who(out, 1)) == [X, Y]


# ---- scarcest place first, fairness --------------------------------------------------------------


def test_the_place_with_fewer_candidates_is_filled_first() -> None:
    a, b = place(1), place(2, start=1)  # overlap, so one person cannot do both
    # X could do either; Y only A. Filling A first with X (the better score) would leave B empty.
    answers = {(1, X): "vil", (1, Y): "kan", (2, X): "vil"}
    allowed = {(1, X), (1, Y), (2, X)}
    out = suggest(build([a, b], [person(X), person(Y)], answers, allowed))

    assert who(out, 1) == [Y] and who(out, 2) == [X]
    assert out.unfilled == []


def test_places_spread_over_people_before_anyone_gets_a_second_shift() -> None:
    places = [place(1, 0), place(2, 1)]
    answers = {(s, v): "vil" for s in (1, 2) for v in (X, Y)}
    out = suggest(build(places, [person(X), person(Y)], answers))

    assert sorted(pick.volunteer_id for pick in out.picks) == [X, Y]


def test_vil_comes_before_kan() -> None:
    out = suggest(
        build([place(1)], [person(X), person(Y)], {(1, X): "kan", (1, Y): "vil"})
    )
    assert who(out, 1) == [Y]


def test_fewer_shifts_so_far_this_semester_goes_first() -> None:
    out = suggest(
        build(
            [place(1)],
            [person(X, history=5), person(Y, history=1)],
            {(1, X): "vil", (1, Y): "vil"},
        )
    )
    assert who(out, 1) == [Y]


def test_helpfulness_vil_goes_to_the_most_helpful_and_kan_to_the_least_asked() -> None:
    helpful, quiet = person(X, offers=9), person(Y, offers=1)
    assert who(
        suggest(build([place(1)], [helpful, quiet], {(1, X): "vil", (1, Y): "vil"})), 1
    ) == [X]
    assert who(
        suggest(build([place(1)], [helpful, quiet], {(1, X): "kan", (1, Y): "kan"})), 1
    ) == [Y]


def test_someone_who_already_has_a_shift_this_week_comes_after_someone_who_has_none() -> (
    None
):
    out = suggest(
        build(
            [place(1, 2)],
            [person(X, week_shifts=1), person(Y)],
            {(1, X): "vil", (1, Y): "kan"},
        )
    )
    assert who(out, 1) == [Y]


# ---- the swap steps (on prepared states) ---------------------------------------------------------


def _state(places, people, answers, holders):
    state = engine._State(build(places, people, answers))
    state.holder.update(holders)
    return state


def test_an_empty_place_is_filled_by_moving_someone_whose_place_has_another_candidate() -> (
    None
):
    empty, held = place(1, start=1), place(2)  # overlap
    answers = {(1, X): "vil", (2, X): "vil", (2, Y): "kan"}
    allowed = {(1, X), (2, X), (2, Y)}
    state = engine._State(
        build([empty, held], [person(X), person(Y)], answers, allowed)
    )
    state.holder[held.key] = X  # X holds the place that blocks them from the empty one

    changed = engine._fill_by_swapping(state, [empty, held])

    assert changed and state.holder == {empty.key: X, held.key: Y}


def test_no_swap_when_nobody_else_can_take_the_blocking_place() -> None:
    empty, held = place(1, start=1), place(2)
    answers = {(1, X): "vil", (2, X): "vil"}
    state = engine._State(build([empty, held], [person(X)], answers))
    state.holder[held.key] = X

    assert not engine._fill_by_swapping(state, [empty, held])
    assert state.holder == {held.key: X}


def test_someone_with_no_shift_takes_a_place_from_someone_with_two() -> None:
    a, b = place(1, 0), place(2, 1)
    answers = {(1, X): "vil", (2, X): "vil", (1, Y): "kan"}
    allowed = {(1, X), (2, X), (1, Y)}
    state = engine._State(build([a, b], [person(X), person(Y)], answers, allowed))
    state.holder.update({a.key: X, b.key: X})

    changed = engine._spread(state, [a, b])

    assert changed and state.holder == {a.key: Y, b.key: X}


def test_a_swap_that_would_leave_the_other_with_nothing_is_not_made() -> None:
    a = place(1)
    answers = {(1, X): "vil", (1, Y): "vil"}
    state = engine._State(build([a], [person(X), person(Y)], answers))
    state.holder[a.key] = X  # X has one shift only: not taken away for Y

    assert not engine._spread(state, [a])
    assert state.holder == {a.key: X}


# ---- the output ----------------------------------------------------------------------------------


def test_the_reason_says_who_why_and_how_many_shifts() -> None:
    out = suggest(build([place(1)], [person(X, history=2)], {(1, X): "vil"}))

    assert out.picks[0].why == (
        "Valgt fordi Person1 vil jobbe dette skiftet, var den eneste som kunne, "
        "har ingen andre skift denne uka og har hatt 2 skift hittil i semesteret."
    )


def test_the_reason_for_a_kan_and_no_shifts_so_far() -> None:
    places = [place(1, 0), place(2, 1)]
    answers = {(1, X): "kan", (1, Y): "kan", (2, X): "kan"}
    allowed = {(1, X), (1, Y), (2, X)}
    out = suggest(build(places, [person(X), person(Y)], answers, allowed))

    reasons = {pick.volunteer_id: pick.why for pick in out.picks}
    # X is the only one for place 2, so Y takes place 1.
    assert reasons[Y].startswith("Valgt fordi Person2 kan jobbe dette skiftet")
    assert "har ikke hatt skift hittil i semesteret" in reasons[Y]


def test_people_who_offered_but_got_no_shift_are_reported() -> None:
    out = suggest(
        build(
            [place(1)],
            [person(X), person(Y), person(Z)],
            {(1, X): "vil", (1, Y): "kan"},
        )
    )

    assert who(out, 1) == [X]
    assert out.without_shift == [Y]  # Z never offered, so Z is not on the list


# ---- determinism and invariants ------------------------------------------------------------------


def test_the_same_input_in_any_order_gives_the_same_suggestions() -> None:
    places = [place(s, s % 5, seat=0) for s in range(1, 9)]
    people = [person(v, history=v % 3, offers=v % 4) for v in range(1, 9)]
    answers = {
        (p.slot_id, q.volunteer_id): ("vil", "kan", "nei", None)[
            (p.slot_id + q.volunteer_id) % 4
        ]
        for p in places
        for q in people
    }
    answers = {k: v for k, v in answers.items() if v}
    first = suggest(build(places, people, answers))

    shuffled_places, shuffled_people = places[:], people[:]
    random.Random(7).shuffle(shuffled_places)
    random.Random(8).shuffle(shuffled_people)
    second = suggest(build(shuffled_places, shuffled_people, answers))

    assert first.picks == second.picks and first.without_shift == second.without_shift


@pytest.mark.parametrize("seed", range(60))
def test_invariants_hold_on_random_weeks(seed: int) -> None:
    rng = random.Random(seed)
    places = [
        place(
            s,
            rng.randint(0, 3),
            start=rng.choice([0, 2, 5]),
            hours=rng.choice([3, 5]),
            seat=rng.randint(0, 1) if s % 5 == 0 else 0,
        )
        for s in range(1, 12)
    ]
    # unique keys: a slot may repeat only with different seats
    places = list({p.key: p for p in places}.values())
    people = [
        person(
            v,
            week_shifts=rng.randint(0, 1),
            history=rng.randint(0, 6),
            offers=rng.randint(0, 8),
            max_week_shifts=rng.choice([None, None, 2]),
        )
        for v in range(1, 9)
    ]
    answers = {}
    for p in places:
        for q in people:
            answer = rng.choice(["vil", "kan", "nei", None])
            if answer:
                answers[(p.slot_id, q.volunteer_id)] = answer
    allowed = {
        (p.slot_id, q.volunteer_id)
        for p in places
        for q in people
        if rng.random() < 0.8
    }
    data = build(places, people, answers, allowed)

    out = suggest(data)

    spec = {p.key: p for p in places}
    taken = set()
    per_person: dict[int, list[PlaceSpec]] = {}
    for pick in out.picks:
        # one pick per place, only places that exist, only allowed people who offered
        keys = [p.key for p in places if p.slot_id == pick.slot_id]
        assert keys
        per_person.setdefault(pick.volunteer_id, []).append(
            next(spec[k] for k in keys if k not in taken and spec[k])
        )
        taken.add(next(k for k in keys if k not in taken))
        assert (pick.slot_id, pick.volunteer_id) in data.allowed
        assert data.answers[(spec[keys[0]].shift_id, pick.volunteer_id)] in (
            "vil",
            "kan",
        )
    assert len(out.picks) + len(out.unfilled) == len(places)
    for volunteer_id, mine in per_person.items():
        mine.sort(key=lambda p: p.starts_at)
        for earlier, later in zip(mine, mine[1:]):
            assert earlier.ends_at <= later.starts_at, (
                "overlapping shifts for one person"
            )
        limit = next(
            p.max_week_shifts for p in people if p.volunteer_id == volunteer_id
        )
        used = next(p.week_shifts for p in people if p.volunteer_id == volunteer_id)
        if limit is not None:
            assert used + len(mine) <= max(limit, used)
    assert out == suggest(data)  # deterministic
