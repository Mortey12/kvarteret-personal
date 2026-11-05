"""The suggestion engine, plan A: Google OR-Tools CP-SAT looks at the whole week at once.

Same input and output as ``suggest.suggest`` (plain Python, plan B), so either can be used. The
hard rules are the same and are never traded away: a person is only a candidate for a place when
the server allows it, they answered "vil" or "kan" for that shift, and they are free then. No one
is on two overlapping places, and no one goes past their own weekly maximum.

**Goal, in order of weight.**

1. Fill as many places as possible.
2. Spread the shifts over people: a person's first shift costs less than their second, which costs
   less than their third, and so on (a convex cost), counting the shifts they already have this
   week. This is what makes everyone who offered get something before anyone gets a second one.
3. Prefer "vil" over "kan", people with fewer shifts so far this semester, and helpfulness (someone
   who has offered a lot lately gets their "vil" shifts first, someone who offered little is asked
   for "kan" shifts first).
4. A tiny cost on the id breaks the last ties, and the solver runs with one worker and a fixed seed,
   so the same input gives the same output.

``ortools`` is imported only when this engine runs, so the app starts without it. If it is missing,
or the solver finds nothing within the time limit, the caller falls back to plain Python
(``suggest_best``).
"""

from __future__ import annotations

from app.domain.shifts.suggest import (
    ANSWER_RANK,
    KIND_RANK,
    PlaceSpec,
    SuggestInput,
    SuggestOutput,
    _finish,
    _overlap,
    _State,
    suggest,
)

TIME_LIMIT_SECONDS = 10.0
FILL = 1_000_000  # one more place filled beats every other consideration together
SHIFT_STEP = (
    4_000  # the cost of a person's n-th shift this week grows by this per shift
)
KAN = 300
HISTORY = 20
HISTORY_CAP = 30
HELP = 5
HELP_CAP = 10
SKIFTLEDER_BONUS = 50  # when places are short, the skiftleder place is the one to fill


class SolverUnavailable(RuntimeError):
    """OR-Tools is not installed, or the solver gave no answer in time."""


def suggest_cpsat(data: SuggestInput) -> SuggestOutput:
    try:
        from ortools.sat.python import cp_model  # noqa: PLC0415 - only loaded when asked for
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise SolverUnavailable("ortools er ikke installert") from error

    state = _State(data)
    order = sorted(
        data.places,
        key=lambda p: (
            len(state.candidates(p)),
            KIND_RANK.get(p.kind, 1),
            p.starts_at,
            p.slot_id,
            p.seat,
        ),
    )

    model = cp_model.CpModel()
    pairs: dict[tuple[tuple[int, int], int], object] = {}
    by_person: dict[int, list[PlaceSpec]] = {}
    for place in data.places:
        for person in state.candidates(place):
            if any(_overlap((place.starts_at, place.ends_at), b) for b in person.busy):
                continue
            pairs[(place.key, person.volunteer_id)] = model.NewBoolVar(
                f"x_{place.slot_id}_{place.seat}_{person.volunteer_id}"
            )
            by_person.setdefault(person.volunteer_id, []).append(place)

    # One person per place.
    for place in data.places:
        taken = [var for (key, _), var in pairs.items() if key == place.key]
        if taken:
            model.AddAtMostOne(taken)

    cost_terms = []
    for person in data.people:
        mine = by_person.get(person.volunteer_id, [])
        if not mine:
            continue
        vars_ = [pairs[(place.key, person.volunteer_id)] for place in mine]
        # No two overlapping places for the same person.
        for i, a in enumerate(mine):
            for b in mine[i + 1 :]:
                if _overlap((a.starts_at, a.ends_at), (b.starts_at, b.ends_at)):
                    model.AddAtMostOne(
                        pairs[(a.key, person.volunteer_id)],
                        pairs[(b.key, person.volunteer_id)],
                    )
        if person.max_week_shifts is not None:
            model.Add(sum(vars_) <= max(0, person.max_week_shifts - person.week_shifts))
        # Convex cost: "has at least j more shifts" flags, each costing more than the last.
        flags = [
            model.NewBoolVar(f"n_{person.volunteer_id}_{j}") for j in range(len(vars_))
        ]
        model.Add(sum(vars_) == sum(flags))
        for j in range(1, len(flags)):
            model.AddImplication(flags[j], flags[j - 1])
        for j, flag in enumerate(flags):
            level = person.week_shifts + j  # shifts they have before this one
            cost_terms.append(flag * (SHIFT_STEP * level + SHIFT_STEP // 2))

    objective = []
    rank = {
        p.volunteer_id: n
        for n, p in enumerate(sorted(data.people, key=lambda p: p.volunteer_id))
    }
    for (key, volunteer_id), var in pairs.items():
        place = state.places[key]
        person = state.people[volunteer_id]
        answer = data.answers[(place.shift_id, volunteer_id)]
        cost = 0
        if answer == "kan":
            cost += KAN * ANSWER_RANK[answer]
        cost += HISTORY * min(person.history, HISTORY_CAP)
        helpful = min(person.offers, HELP_CAP)
        cost += -HELP * helpful if answer == "vil" else HELP * helpful
        cost += rank[volunteer_id] // 8  # last tie-break, small and fixed
        reward = FILL + (SKIFTLEDER_BONUS if place.kind == "skiftleder" else 0)
        objective.append(var * (reward - cost))
    model.Maximize(sum(objective) - sum(cost_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = TIME_LIMIT_SECONDS
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SolverUnavailable("løseren fant ingen løsning")

    for (key, volunteer_id), var in pairs.items():
        if solver.Value(var):
            state.holder[key] = volunteer_id
    return _finish(state, order)


def suggest_best(data: SuggestInput, engine: str = "simple") -> SuggestOutput:
    """The engine named by the setting ("cpsat" or "simple"); plain Python if the solver cannot run."""
    if engine == "cpsat":
        try:
            return suggest_cpsat(data)
        except SolverUnavailable:
            pass
    return suggest(data)
