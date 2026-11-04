"""The suggestion engine ("Lag forslag for uka"): plain Python, deterministic, no database.

Plan B of the design notes (decided 2026-11-02): "scarcest place first", then an improvement pass
with swaps. The input and output name no solver, so a CP-SAT engine (plan A) can replace
``suggest`` without touching anything around it.

**Hard rules.** A person is a candidate for a place only when all of these hold; the engine never
breaks one, it leaves the place empty instead.

- the server says the person may take the place (``allowed``: qualification, the venue's age
  limit, shift not closed, not already on the shift; the rules stay on the server);
- the person answered "vil" or "kan" for that shift (never "kan ikke", and never unanswered:
  nobody is suggested for a shift they did not offer to work);
- no overlap in time with a shift the person already has, or with another suggestion;
- the person's own weekly maximum, when one is given.

Nothing the leaders placed is touched, and no place is added.

**Goal and fairness.** Everyone who answered should get a shift, not only the places filled. Among
candidates, the order of preference is: fewest shifts this week (so the places spread over
people first), "vil" before "kan", fewest shifts so far this semester, and helpfulness: someone
who has offered a lot lately gets their "vil" shifts first, someone who offered little is asked
for "kan" shifts first. The last tie-break is the id, so the same input always gives the same output.

**Steps.**

1. Places in order of scarcity (fewest candidates first; skiftleder before the rest; then time).
   Each takes the best free candidate.
2. A place still empty is filled by moving a candidate X from the place Q they were given when
   Q has another candidate Y who is free (X to the empty place, Y to Q).
3. A person with no shift at all this week takes over a place from someone who has two or more.

Every pick carries a "Valgt fordi" sentence built from the facts that decided it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

ANSWER_RANK = {"vil": 0, "kan": 1}
# The kind that is hardest to fill goes first when two places are equally scarce.
KIND_RANK = {"skiftleder": 0}
MAX_PASSES = 4


@dataclass(frozen=True, slots=True)
class PlaceSpec:
    """One EMPTY place that has to be filled. A slot with room for three has seats 0, 1 and 2."""

    slot_id: int
    seat: int
    shift_id: int
    kind: str
    starts_at: datetime
    ends_at: datetime

    @property
    def key(self) -> tuple[int, int]:
        return (self.slot_id, self.seat)

    @property
    def day(self) -> date:
        return self.starts_at.date()


@dataclass(frozen=True, slots=True)
class PersonSpec:
    volunteer_id: int
    name: str
    first_name: str = ""
    # Shifts the person already has this week (placed by a leader), and when they are.
    week_shifts: int = 0
    busy: tuple[tuple[datetime, datetime], ...] = ()
    # Shifts so far this semester, and "vil" or "kan" answers in the last weeks.
    history: int = 0
    offers: int = 0
    # The person's own weekly maximum, when one is known (None: no limit).
    max_week_shifts: int | None = None


@dataclass(frozen=True, slots=True)
class SuggestInput:
    places: tuple[PlaceSpec, ...]
    people: tuple[PersonSpec, ...]
    # (shift_id, volunteer_id) -> "vil" | "kan" | "nei"; no entry means not answered.
    answers: Mapping[tuple[int, int], str]
    # (slot_id, volunteer_id) pairs the server's rules allow.
    allowed: frozenset[tuple[int, int]]


@dataclass(frozen=True, slots=True)
class Pick:
    slot_id: int
    volunteer_id: int
    why: str


@dataclass(slots=True)
class SuggestOutput:
    picks: list[Pick] = field(default_factory=list)
    # Places nobody could take.
    unfilled: list[PlaceSpec] = field(default_factory=list)
    # People who offered ("vil" or "kan") for something this week and got no shift at all.
    without_shift: list[int] = field(default_factory=list)


def _overlap(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


class _State:
    """The picks so far, and the questions the steps ask about them."""

    def __init__(self, data: SuggestInput) -> None:
        self.data = data
        self.people = {p.volunteer_id: p for p in data.people}
        self.places = {p.key: p for p in data.places}
        self.holder: dict[tuple[int, int], int] = {}  # place key -> volunteer id

    def candidates(self, place: PlaceSpec) -> list[PersonSpec]:
        return [
            person
            for person in self.data.people
            if (place.slot_id, person.volunteer_id) in self.data.allowed
            and self.data.answers.get((place.shift_id, person.volunteer_id))
            in ANSWER_RANK
        ]

    def picked_by(self, volunteer_id: int) -> list[PlaceSpec]:
        return [
            self.places[key] for key, who in self.holder.items() if who == volunteer_id
        ]

    def load(self, volunteer_id: int) -> int:
        return self.people[volunteer_id].week_shifts + len(self.picked_by(volunteer_id))

    def conflicts(
        self,
        person: PersonSpec,
        place: PlaceSpec,
        *,
        ignoring: tuple[int, int] | None = None,
    ) -> bool:
        """Would ``place`` break a hard rule for this person, not counting the pick at ``ignoring``?"""
        span = (place.starts_at, place.ends_at)
        if any(_overlap(span, busy) for busy in person.busy):
            return True
        mine = [p for p in self.picked_by(person.volunteer_id) if p.key != ignoring]
        if any(_overlap(span, (p.starts_at, p.ends_at)) for p in mine):
            return True
        if (
            person.max_week_shifts is not None
            and person.week_shifts + len(mine) >= person.max_week_shifts
        ):
            return True
        return False

    def score(
        self,
        person: PersonSpec,
        place: PlaceSpec,
        *,
        ignoring: tuple[int, int] | None = None,
    ) -> tuple:
        answer = self.data.answers[(place.shift_id, person.volunteer_id)]
        load = person.week_shifts + len(
            [p for p in self.picked_by(person.volunteer_id) if p.key != ignoring]
        )
        # Helpful people first for "vil", least asked first for "kan".
        helpfulness = -person.offers if answer == "vil" else person.offers
        return (
            load,
            ANSWER_RANK[answer],
            person.history,
            helpfulness,
            person.volunteer_id,
        )

    def best(
        self, place: PlaceSpec, *, exclude: frozenset[int] = frozenset()
    ) -> PersonSpec | None:
        free = [
            c
            for c in self.candidates(place)
            if c.volunteer_id not in exclude and not self.conflicts(c, place)
        ]
        return min(free, key=lambda c: self.score(c, place), default=None)


def suggest(data: SuggestInput) -> SuggestOutput:
    """Suggestions for the empty places in ``data``. Deterministic: the same input, the same output."""
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

    # 1. Scarcest place first, each to the best free candidate.
    for place in order:
        person = state.best(place)
        if person is not None:
            state.holder[place.key] = person.volunteer_id

    for _ in range(MAX_PASSES):
        changed = _fill_by_swapping(state, order)
        changed |= _spread(state, order)
        if not changed:
            break

    picks = [
        Pick(place.slot_id, state.holder[place.key], _why(state, place))
        for place in sorted(data.places, key=lambda p: (p.starts_at, p.slot_id, p.seat))
        if place.key in state.holder
    ]
    unfilled = [place for place in order if place.key not in state.holder]
    offered = {
        person.volunteer_id
        for person in data.people
        if any(
            (place.slot_id, person.volunteer_id) in data.allowed
            and data.answers.get((place.shift_id, person.volunteer_id)) in ANSWER_RANK
            for place in data.places
        )
    }
    without = sorted(v for v in offered if state.load(v) == 0)
    return SuggestOutput(picks, unfilled, without)


def _fill_by_swapping(state: _State, order: list[PlaceSpec]) -> bool:
    """Step 2: an empty place is filled by moving X from the place Q they hold when Q has a free Y."""
    changed = False
    for empty in order:
        if empty.key in state.holder:
            continue
        for x in sorted(state.candidates(empty), key=lambda c: state.score(c, empty)):
            blocking = [
                q
                for q in state.picked_by(x.volunteer_id)
                if _overlap((empty.starts_at, empty.ends_at), (q.starts_at, q.ends_at))
            ]
            if len(blocking) != 1:
                continue
            q = blocking[0]
            if state.conflicts(x, empty, ignoring=q.key):
                continue
            y = state.best(q, exclude=frozenset({x.volunteer_id}))
            if y is None:
                continue
            state.holder[q.key] = y.volunteer_id
            state.holder[empty.key] = x.volunteer_id
            changed = True
            break
    return changed


def _spread(state: _State, order: list[PlaceSpec]) -> bool:
    """Step 3: someone with no shift this week takes a place from someone who has two or more."""
    changed = False
    idle = sorted(
        (p for p in state.data.people if state.load(p.volunteer_id) == 0),
        key=lambda p: (p.history, p.volunteer_id),
    )
    for person in idle:
        if state.load(person.volunteer_id) != 0:
            continue
        for place in order:
            holder_id = state.holder.get(place.key)
            if holder_id is None or state.load(holder_id) < 2:
                continue
            if person not in state.candidates(place) or state.conflicts(person, place):
                continue
            state.holder[place.key] = person.volunteer_id
            changed = True
            break
    return changed


def _why(state: _State, place: PlaceSpec) -> str:
    """ "Valgt fordi ...": the facts that decided it, in the leader's words."""
    person = state.people[state.holder[place.key]]
    first = person.first_name or person.name.split(" ")[0]
    answer = state.data.answers[(place.shift_id, person.volunteer_id)]
    reasons = [f"{first} {'vil' if answer == 'vil' else 'kan'} jobbe dette skiftet"]
    others = state.load(person.volunteer_id) - 1
    if len(state.candidates(place)) == 1:
        reasons.append("var den eneste som kunne")
    if others == 0:
        reasons.append("har ingen andre skift denne uka")
    else:
        reasons.append(
            f"har {others} annet skift denne uka"
            if others == 1
            else f"har {others} andre skift denne uka"
        )
    if person.history == 0:
        reasons.append("har ikke hatt skift hittil i semesteret")
    else:
        reasons.append(f"har hatt {person.history} skift hittil i semesteret")
    return "Valgt fordi " + ", ".join(reasons[:-1]) + " og " + reasons[-1] + "."
