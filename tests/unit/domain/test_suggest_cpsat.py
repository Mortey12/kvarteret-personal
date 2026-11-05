"""The OR-Tools engine (plan A) against the plain Python one (plan B): same hard rules, never
fewer places filled, deterministic, and a fallback when the solver cannot run."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("ortools")

from app.domain.shifts import suggest_cpsat as cpsat  # noqa: E402
from app.domain.shifts.suggest import (  # noqa: E402
    PersonSpec,
    PlaceSpec,
    SuggestInput,
    suggest,
)

MONDAY = datetime(2026, 11, 2, 16, tzinfo=UTC)


def random_week(seed: int) -> SuggestInput:
    rng = random.Random(seed)
    places = []
    for slot in range(1, rng.randint(8, 16)):
        begin = MONDAY + timedelta(days=rng.randint(0, 3), hours=rng.choice([0, 2, 5]))
        places.append(
            PlaceSpec(
                slot,
                rng.randint(0, 1) if slot % 5 == 0 else 0,
                slot * 10,
                rng.choice(["skiftleder", "skjenker", "skjenker", "skjenker"]),
                begin,
                begin + timedelta(hours=rng.choice([3, 5])),
            )
        )
    places = list({p.key: p for p in places}.values())
    people = tuple(
        PersonSpec(
            v,
            f"Person{v} Etternavn",
            f"Person{v}",
            week_shifts=rng.randint(0, 1),
            history=rng.randint(0, 6),
            offers=rng.randint(0, 8),
            max_week_shifts=rng.choice([None, None, 2]),
        )
        for v in range(1, rng.randint(6, 12))
    )
    answers = {}
    for p in places:
        for q in people:
            answer = rng.choice(["vil", "kan", "nei", None])
            if answer:
                answers[(p.shift_id, q.volunteer_id)] = answer
    allowed = frozenset(
        (p.slot_id, q.volunteer_id)
        for p in places
        for q in people
        if rng.random() < 0.8
    )
    return SuggestInput(tuple(places), people, answers, allowed)


def assert_hard_rules(data: SuggestInput, out) -> None:
    by_slot: dict[int, list[PlaceSpec]] = {}
    for p in data.places:
        by_slot.setdefault(p.slot_id, []).append(p)
    used: dict[int, int] = {}
    spans: dict[int, list[tuple[datetime, datetime]]] = {}
    for pick in out.picks:
        place = by_slot[pick.slot_id][used.get(pick.slot_id, 0)]
        used[pick.slot_id] = used.get(pick.slot_id, 0) + 1
        assert (pick.slot_id, pick.volunteer_id) in data.allowed
        assert data.answers[(place.shift_id, pick.volunteer_id)] in ("vil", "kan")
        spans.setdefault(pick.volunteer_id, []).append((place.starts_at, place.ends_at))
        assert pick.why
    people = {p.volunteer_id: p for p in data.people}
    for volunteer_id, mine in spans.items():
        person = people[volunteer_id]
        for busy in person.busy:
            assert all(not (s < busy[1] and busy[0] < e) for s, e in mine)
        mine.sort()
        for (_, end), (start, _) in zip(mine, mine[1:]):
            assert end <= start, "overlapping shifts for one person"
        if person.max_week_shifts is not None:
            assert person.week_shifts + len(mine) <= max(
                person.max_week_shifts, person.week_shifts
            )
    assert len(out.picks) + len(out.unfilled) == len(data.places)


@pytest.mark.parametrize("seed", range(60))
def test_hard_rules_and_never_fewer_places_filled(seed: int) -> None:
    data = random_week(seed)
    solver = cpsat.suggest_cpsat(data)
    plain = suggest(data)
    assert_hard_rules(data, solver)
    assert len(solver.picks) >= len(plain.picks)


@pytest.mark.parametrize("seed", range(10))
def test_same_input_same_output(seed: int) -> None:
    data = random_week(seed)
    assert cpsat.suggest_cpsat(data) == cpsat.suggest_cpsat(data)


def test_solver_gives_everyone_a_first_shift_before_anyone_a_second() -> None:
    def span(day: int):
        return MONDAY + timedelta(days=day), MONDAY + timedelta(days=day, hours=4)

    places = tuple(PlaceSpec(s, 0, s * 10, "skjenker", *span(s)) for s in (1, 2))
    people = tuple(PersonSpec(v, f"P{v} E", f"P{v}") for v in (1, 2))
    answers = {(s * 10, v): "vil" for s in (1, 2) for v in (1, 2)}
    allowed = frozenset((s, v) for s in (1, 2) for v in (1, 2))
    out = cpsat.suggest_cpsat(SuggestInput(places, people, answers, allowed))
    assert sorted(p.volunteer_id for p in out.picks) == [1, 2]


def test_falls_back_to_plain_python_when_the_solver_cannot_run(monkeypatch) -> None:
    def broken(_data):
        raise cpsat.SolverUnavailable("ingen løser")

    monkeypatch.setattr(cpsat, "suggest_cpsat", broken)
    data = random_week(3)
    assert cpsat.suggest_best(data, "cpsat") == suggest(data)
    assert cpsat.suggest_best(data, "simple") == suggest(data)
