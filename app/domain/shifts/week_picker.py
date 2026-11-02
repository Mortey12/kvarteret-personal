"""Pure builders for placing people from the leader's week page (L2a). No I/O.

Two views of the same question, "can this person take this place, and what should the leader be
told?": the picker for one place (``picker_rows``) and the "Sett inn <fornavn>" state for every
person and open place that the page embeds for person-first placing (``build_place_state``).

Nothing here is a second copy of the rules. Hard rules come from the same functions the old board
uses: ``EligibilityContext.problem`` (``placement_problem``), ``age_problem`` and the closed and
"already on the shift" checks of ``ShiftsService._check``, in the same order. Warnings never block;
they are the texts the leader sees before and after a hand placement. The server still checks every
rule again when the place is taken, so these answers are only what the page shows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.domain.shifts.planning import (
    MISSING_BIRTH_DATE_WARNING,
    VENUE_MIN_AGE,
    EligibilityContext,
    age_problem,
)
from app.domain.shifts.week_view import ANSWER_WORDS, RED, YELLOW

CLOSED_REASON = "Skiftet er stengt"
ALREADY_ON_SHIFT_REASON = "Står allerede på dette skiftet"
# vil first, kan, not answered, kan ikke at the bottom.
ANSWER_ORDER = {"vil": 0, "kan": 1, None: 2, "nei": 3}


@dataclass(frozen=True, slots=True)
class Warn:
    text: str
    level: str


@dataclass(frozen=True, slots=True)
class Candidate:
    """One person as seen from the shown week, before any placement."""

    volunteer_id: int
    name: str
    is_sl: bool
    eligibility: EligibilityContext
    # Shifts this week in every group, and this month, before the placement.
    week_count: int = 0
    month_count: int = 0
    answers: dict[int, str] = field(default_factory=dict)  # shift id -> "vil" / "kan" / "nei"
    # Oslo day -> shifts that day in every group.
    shifts_per_day: dict[date, int] = field(default_factory=dict)
    birth_date: date | None = None
    swap_requested: bool = False


@dataclass(frozen=True, slots=True)
class PlaceFacts:
    """One place on one shift."""

    shift_id: int
    group_id: int
    venue: str
    day: date
    is_closed: bool
    kind: str
    open_for_samf: bool
    slot_id: int = 0
    on_shift: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class Fit:
    ok: bool
    reason: str = ""
    warnings: tuple[Warn, ...] = ()

    @property
    def level(self) -> str:
        levels = {warning.level for warning in self.warnings}
        return RED if RED in levels else (YELLOW if YELLOW in levels else "")


def hard_problem(person: Candidate, place: PlaceFacts) -> str | None:
    """The hard rule the person breaks for this place, in the order ``ShiftsService._check`` uses."""
    if place.is_closed:
        return CLOSED_REASON
    if person.volunteer_id in place.on_shift:
        return ALREADY_ON_SHIFT_REASON
    problem = person.eligibility.problem(place.kind, place.group_id, place.open_for_samf)
    if problem:
        return problem
    return age_problem(place.venue, person.birth_date, place.day)


def warnings_for(person: Candidate, place: PlaceFacts) -> tuple[Warn, ...]:
    """Warnings only. Red are real conflicts, yellow the rest. Red first."""
    found: list[Warn] = []
    if person.answers.get(place.shift_id) == "nei":
        found.append(Warn("svarte kan ikke", RED))
    if person.shifts_per_day.get(place.day, 0) >= 1:
        found.append(Warn("to skift samme dag", RED))
    if person.week_count >= 1:
        found.append(Warn(f"skift nummer {person.week_count + 1} denne uka", YELLOW))
    if person.swap_requested:
        found.append(Warn("ønsker bytte", YELLOW))
    if place.venue in VENUE_MIN_AGE and person.birth_date is None:
        found.append(Warn(MISSING_BIRTH_DATE_WARNING, YELLOW))
    return tuple(sorted(found, key=lambda item: item.level != RED))


def evaluate(person: Candidate, place: PlaceFacts) -> Fit:
    reason = hard_problem(person, place)
    if reason:
        return Fit(False, reason)
    return Fit(True, "", warnings_for(person, place))


@dataclass(frozen=True, slots=True)
class PickerRow:
    volunteer_id: int
    name: str
    is_sl: bool
    answer: str
    answer_word: str  # empty for "ikke svart": the list says nothing about it
    week_count: int
    month_count: int
    warnings: tuple[Warn, ...]

    @property
    def level(self) -> str:
        levels = {warning.level for warning in self.warnings}
        return RED if RED in levels else (YELLOW if YELLOW in levels else "")


def sort_key(row: PickerRow) -> tuple:
    """Answer group first, then fewer shifts this week, fewer this month, then name."""
    return (
        ANSWER_ORDER.get(row.answer or None, ANSWER_ORDER[None]),
        row.week_count,
        row.month_count,
        row.name.casefold(),
        row.volunteer_id,
    )


def picker_rows(people: list[Candidate], place: PlaceFacts) -> list[PickerRow]:
    """The people who fit the place, sorted. People who break a hard rule are left out."""
    rows: list[PickerRow] = []
    for person in people:
        fit = evaluate(person, place)
        if not fit.ok:
            continue
        answer = person.answers.get(place.shift_id) or ""
        rows.append(
            PickerRow(
                volunteer_id=person.volunteer_id,
                name=person.name,
                is_sl=person.is_sl,
                answer=answer,
                answer_word=ANSWER_WORDS.get(answer, ""),
                week_count=person.week_count,
                month_count=person.month_count,
                warnings=fit.warnings,
            )
        )
    return sorted(rows, key=sort_key)


def build_place_state(people: list[Candidate], places: list[PlaceFacts]) -> dict[str, dict[str, dict]]:
    """Person first: for every person and open place, whether the place fits and what to warn about.

    ``{volunteer id: {slot id: {"ok": 1, "w": [[text, level], ...]} or {"ok": 0, "why": text}}}``.
    The page's JavaScript only shows it.
    """
    state: dict[str, dict[str, dict]] = {}
    for person in people:
        per_slot: dict[str, dict] = {}
        for place in places:
            fit = evaluate(person, place)
            per_slot[str(place.slot_id)] = (
                {"ok": 1, "w": [[item.text, item.level] for item in fit.warnings]}
                if fit.ok
                else {"ok": 0, "why": fit.reason}
            )
        state[str(person.volunteer_id)] = per_slot
    return state


def live_message(message: str, problems: list[str]) -> str:
    """The result text for the live region: the service's sentence, then "Advarsel: ..." if any.

    The service puts its own hints after " Merk: "; the page says the same things (and more) from
    the placed person's problems, so that tail is replaced.
    """
    core = message.partition(" Merk: ")[0].rstrip()
    if not problems:
        return core
    return f"{core} Advarsel: {', '.join(problems)}."
