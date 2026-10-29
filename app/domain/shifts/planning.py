"""Pure planning rules for Skiftlista: no database, easy to test.

The same rules live in the clickable prototype (mstene.no/skiftliste,
``logic.js``) and in the app, so keep them in step when they change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.shifts.permissions import participating_siblings, person_area
from app.domain.shifts.models import (
    AssignedPerson,
    DayBlock,
    ShiftRecord,
    ShiftView,
    SlotRecord,
    SlotView,
    VenueBlock,
)

OSLO = ZoneInfo("Europe/Oslo")


@dataclass(frozen=True, slots=True)
class SlotKind:
    label: str
    # Crew places are the regular working places an opplæring place can replace.
    crew: bool
    tag: str


SLOT_KINDS: dict[str, SlotKind] = {
    "skiftleder": SlotKind("Skiftleder", crew=False, tag="Kun skiftledere"),
    "skjenker": SlotKind("Skjenker", crew=True, tag="Skjenker"),
    "bartender": SlotKind("Bartender", crew=True, tag="Bartender"),
    "kokk": SlotKind("Kokk", crew=True, tag="Kun kokker"),
    "opplaering": SlotKind("Opplæring", crew=False, tag="Opplæring"),
    "garderobe": SlotKind("Garderobe", crew=False, tag="Garderobe"),
}

# Places that need a verv. They never open for samfunnettjeneste.
RESTRICTED_KINDS = frozenset({"skiftleder", "kokk", "opplaering"})
# Places where barkurs counts (for samfunnettjeneste).
BAR_KINDS = frozenset({"skjenker", "bartender"})

# Minimum age per venue. The app shows only "20+", never a birth date.
VENUE_MIN_AGE = {"Halvtimen": 20}

# Venue names shown on the plan. Kitchen shifts happen in Stjernesalen.
VENUE_LABELS = {"Stjernesalen": "Stjernesalen og kjøkken"}
VENUE_ORDER = ["Stjernesalen", "Grøndahls", "Halvtimen"]

WEEKDAYS_NO = ["Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Lørdag", "Søndag"]
MONTHS_NO = [
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
]


def slot_kind(kind: str) -> SlotKind:
    return SLOT_KINDS.get(kind, SlotKind(kind.capitalize(), crew=False, tag=kind.capitalize()))


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def format_day(day: date) -> str:
    return f"{WEEKDAYS_NO[day.weekday()]} {day.day}. {MONTHS_NO[day.month - 1]}"


def format_week(week_start: date) -> str:
    week_end = week_start + timedelta(days=6)
    week_number = week_start.isocalendar().week
    if week_start.month == week_end.month:
        span = f"{week_start.day}.–{week_end.day}. {MONTHS_NO[week_end.month - 1]}"
    else:
        span = (
            f"{week_start.day}. {MONTHS_NO[week_start.month - 1]}–"
            f"{week_end.day}. {MONTHS_NO[week_end.month - 1]}"
        )
    return f"Uke {week_number} · {span}"


def format_time_range(starts_at: datetime, ends_at: datetime) -> str:
    start = starts_at.astimezone(OSLO)
    end = ends_at.astimezone(OSLO)
    return f"{start:%H:%M}–{end:%H:%M}"


def _crew_slot(slots: list[SlotRecord]) -> SlotRecord | None:
    return next((slot for slot in slots if slot_kind(slot.kind).crew), None)


def _training_slot(slots: list[SlotRecord]) -> SlotRecord | None:
    return next((slot for slot in slots if slot.kind == "opplaering"), None)


def training_takes_place(shift: ShiftRecord) -> bool:
    """True when opplæring takes one of the regular places on this shift."""
    return (
        shift.training_replaces_slot
        and _crew_slot(shift.slots) is not None
        and _training_slot(shift.slots) is not None
    )


def present(slot: SlotRecord) -> int:
    """People on the place who are coming ("kan ikke i dag" does not count)."""
    return sum(1 for person in slot.people if not person.cannot_attend)


def open_places(shift: ShiftRecord, slot: SlotRecord, *, count_absent: bool = False) -> int:
    """How many more people this place can take.

    When opplæring takes a regular place, the crew place and the opplæring
    place share the crew count: a trainee fills one crew place, and a
    full crew leaves no room for a trainee.

    Someone who has said "kan ikke i dag" leaves their place open, unless
    ``count_absent`` asks for the room as if everyone came.
    """

    def taken(item: SlotRecord) -> int:
        return len(item.people) if count_absent else present(item)

    free = slot.count - taken(slot)
    crew = _crew_slot(shift.slots)
    training = _training_slot(shift.slots)
    if shift.training_replaces_slot and crew is not None and training is not None:
        if slot.slot_id in (crew.slot_id, training.slot_id):
            shared = crew.count - taken(crew) - taken(training)
            free = min(free, shared)
    return max(0, free)


def absent_to_replace(shift: ShiftRecord, slot: SlotRecord) -> AssignedPerson | None:
    """The "kan ikke" person who comes off when someone takes this place.

    Only when the place has no room except the one the absent person left.
    Their own place first; else the partner place they share the crew count with.
    """
    if open_places(shift, slot, count_absent=True) > 0:
        return None
    candidates = [slot]
    crew = _crew_slot(shift.slots)
    training = _training_slot(shift.slots)
    if shift.training_replaces_slot and crew is not None and training is not None:
        if slot.slot_id == crew.slot_id:
            candidates.append(training)
        elif slot.slot_id == training.slot_id:
            candidates.append(crew)
    for item in candidates:
        absent = next((person for person in item.people if person.cannot_attend), None)
        if absent is not None:
            return absent
    return None


def opened_by_absence(slot: SlotRecord) -> bool:
    """True when the only room on this place is one left by "kan ikke i dag"."""
    return any(person.cannot_attend for person in slot.people) and slot.count - len(slot.people) <= 0


def open_for_everyone(slot: SlotRecord) -> bool:
    """Open for samfunnettjeneste: set by the leader, or opened by "kan ikke i dag".

    A place that needs a verv (skiftleder, kokk, opplæring) never opens up.
    """
    if slot.kind in RESTRICTED_KINDS:
        return False
    return slot.open_for_samf or opened_by_absence(slot)


def training_places_used(shift: ShiftRecord, slot: SlotRecord) -> int:
    """Crew places taken by trainees (shown as "Brukt til opplæring")."""
    crew = _crew_slot(shift.slots)
    training = _training_slot(shift.slots)
    if not shift.training_replaces_slot or crew is None or training is None:
        return 0
    if slot.slot_id != crew.slot_id:
        return 0
    return min(crew.count, present(training))


def build_shift_view(shift: ShiftRecord) -> ShiftView:
    slots: list[SlotView] = []
    total_open = 0
    for slot in sorted(shift.slots, key=lambda item: (item.sort_order, item.slot_id)):
        kind = slot_kind(slot.kind)
        free = 0 if shift.is_closed else open_places(shift, slot)
        used = training_places_used(shift, slot)
        if slot.kind != "opplaering":
            total_open += free
        open_flag = slot.open_for_samf or open_for_everyone(slot)
        if slot.kind == "opplaering":
            tag = "Tar en vanlig plass" if training_takes_place(shift) else "I tillegg"
        elif opened_by_absence(slot) and open_for_everyone(slot):
            tag = "Åpen for alle (kan ikke i dag)"
        elif not open_flag and not kind.tag.startswith("Kun"):
            tag = "Kun Skjenke"
        elif open_flag:
            tag = "Åpen for samf."
        else:
            tag = kind.tag
        filled = present(slot) + used
        slots.append(
            SlotView(
                slot_id=slot.slot_id,
                kind=slot.kind,
                label=kind.label,
                count=slot.count,
                filled_text=f"{filled}/{slot.count}" + (f" ({used} opplæring)" if used else ""),
                tag=tag,
                people=list(slot.people),
                open_places=free,
                training_places=used,
                blocked_by_training=(
                    slot.kind == "opplaering"
                    and not shift.is_closed
                    and free == 0
                    and present(slot) < slot.count
                ),
                group_id=shift.group_id,
                open_for_samf=open_flag,
                open_label=_open_label(shift, slot),
            )
        )
    return ShiftView(
        shift_id=shift.shift_id,
        group_name=shift.group_name,
        title=shift.title,
        time_label=format_time_range(shift.starts_at, shift.ends_at),
        note=shift.note,
        is_closed=shift.is_closed,
        training_replaces_slot=shift.training_replaces_slot,
        open_places=total_open,
        slots=slots,
        people_count=sum(len(slot.people) for slot in shift.slots),
    )


def group_into_days(shifts: list[ShiftRecord]) -> list[DayBlock]:
    """Day by day, then venue by venue, in start-time order."""
    ordered = sorted(shifts, key=lambda item: (item.starts_at, item.shift_id))
    days: dict[date, dict[str, list[ShiftRecord]]] = {}
    for shift in ordered:
        day = shift.starts_at.astimezone(OSLO).date()
        days.setdefault(day, {}).setdefault(shift.venue, []).append(shift)
    blocks: list[DayBlock] = []
    for day in sorted(days):
        venues = days[day]
        venue_names = sorted(
            venues,
            key=lambda name: (
                VENUE_ORDER.index(name) if name in VENUE_ORDER else len(VENUE_ORDER),
                name,
            ),
        )
        blocks.append(
            DayBlock(
                day=day,
                label=format_day(day),
                venues=[
                    VenueBlock(
                        label=VENUE_LABELS.get(name, name),
                        shifts=[build_shift_view(shift) for shift in venues[name]],
                    )
                    for name in venue_names
                ],
            )
        )
    return blocks


def _open_label(shift: ShiftRecord, slot: SlotRecord) -> str:
    """The text on an empty place. A trainee place that shares the crew
    count is the same physical place as the empty crew place, so say so."""
    if slot.kind == "opplaering" and training_takes_place(shift):
        crew = _crew_slot(shift.slots)
        crew_label = slot_kind(crew.kind).label.lower() if crew else "vanlig"
        return f"Ledig (tar en {crew_label}plass)"
    return "Ledig"


# The verv that marks someone in training. Eligibility no longer reads it (it
# comes from qualification rows); the app still shows it as ``in_training``.
TRAINEE_ROLE = "Opplæring"

# The qualification each kind of place needs. Garderobe needs none.
NEEDS: dict[str, str | None] = {
    "skiftleder": "skiftleder",
    "kokk": "kokk",
    "skjenker": "skjenker",
    "bartender": "skjenker",
    "garderobe": None,
}

TRAINEE_CREW_PROBLEM = "I opplæring: bruk opplæringsplassen"
TRAINING_PLACE_PROBLEM = "Kun for de som er i opplæring i gruppen"
NOT_IN_AREA_PROBLEM = "Kun for medlemmer av gruppa"

# (qualification, status, group id): one row of ``volunteer_qualifications``.
QualificationRow = tuple[str, str, int]


@dataclass(frozen=True, slots=True)
class PersonEligibility:
    """What decides a person's places: membership, area and qualification rows."""

    member_groups: frozenset[int] = frozenset()
    area: frozenset[int] = frozenset()
    qualifications: tuple[QualificationRow, ...] = ()


@dataclass(frozen=True, slots=True)
class ShiftGroupRules:
    """What the shift's group says about who may come in from other bars."""

    sibling_skiftleder_allowed: bool = False
    # Participating groups with the same parent (not the group itself).
    siblings: frozenset[int] = frozenset()


NO_RULES = ShiftGroupRules()


@dataclass(frozen=True, slots=True)
class EligibilityContext:
    """Everything eligibility needs for one person, built once per request."""

    person: PersonEligibility
    rules: dict[int, ShiftGroupRules]

    def rules_for(self, group_id: int) -> ShiftGroupRules:
        return self.rules.get(group_id, NO_RULES)

    def problem(self, kind: str, shift_group_id: int, open_for_samf: bool) -> str | None:
        return placement_problem(
            kind, shift_group_id, open_for_samf, self.person, self.rules_for(shift_group_id)
        )


def group_rules(
    participating: frozenset[int],
    parent_by_group: Mapping[int, int | None],
    sibling_switches: Mapping[int, bool],
) -> dict[int, ShiftGroupRules]:
    """The rules of every participating group."""
    return {
        group_id: ShiftGroupRules(
            sibling_skiftleder_allowed=bool(sibling_switches.get(group_id, False)),
            siblings=participating_siblings(group_id, participating, parent_by_group),
        )
        for group_id in participating
    }


def person_eligibility(
    member_group_ids: Iterable[int],
    qualifications: Iterable[QualificationRow],
    participating: frozenset[int],
    parent_by_group: Mapping[int, int | None],
) -> PersonEligibility:
    members = frozenset(member_group_ids) & participating
    return PersonEligibility(
        member_groups=members,
        area=person_area(members, participating, parent_by_group),
        qualifications=tuple(qualifications),
    )


def _has(rows: Iterable[QualificationRow], qualification: str, status: str, groups: Iterable[int]) -> bool:
    wanted = set(groups)
    return any(q == qualification and s == status and g in wanted for q, s, g in rows)


def _training_rows(person: PersonEligibility, group_id: int) -> set[str]:
    """Qualifications the person is training for in this group (needs membership).

    A training row does not count when the same qualification is already
    approved in the group: approved wins.
    """
    if group_id not in person.member_groups:
        return set()
    return {
        q
        for q, s, g in person.qualifications
        if s == "training" and g == group_id and not _has(person.qualifications, q, "approved", [g])
    }


def placement_problem(
    kind: str,
    shift_group_id: int,
    open_for_samf: bool,
    person: PersonEligibility,
    rules: ShiftGroupRules = NO_RULES,
) -> str | None:
    """Why this person cannot take this kind of place, or None if they can.

    ``person`` holds the participating groups they are an active member of
    this semester, their area and their qualification rows. ``rules`` is
    about the shift's group: the sibling-skiftleder switch and its
    participating siblings. ``open_for_samf`` is true for a place open to
    everyone (set by the leader, or opened by "kan ikke i dag"). The
    age rules (Halvtimen 20+) are checked by the caller, after this.
    """
    group_id = shift_group_id
    in_area = group_id in person.area
    rows = person.qualifications
    if kind == "opplaering":
        # Step 2 only: membership of the group and a training row for it. Never
        # open to everyone.
        if _training_rows(person, group_id):
            return None
        return TRAINING_PLACE_PROBLEM
    need = NEEDS.get(kind)
    if in_area:
        if need is None:
            return None
        peers = rules.siblings | {group_id}
        if need == "skjenker" and _has(rows, "skjenker", "approved", peers):
            return None
        if need in ("skiftleder", "kokk") and _has(rows, need, "approved", [group_id]):
            return None
        if (
            need == "skiftleder"
            and rules.sibling_skiftleder_allowed
            and _has(rows, "skiftleder", "approved", rules.siblings)
        ):
            return None
        if need in _training_rows(person, group_id) and slot_kind(kind).crew:
            return TRAINEE_CREW_PROBLEM
    if open_for_samf and kind not in RESTRICTED_KINDS:
        return None
    if not in_area or need is None:
        return NOT_IN_AREA_PROBLEM
    return f"Kun for godkjente {need}"


def age_on(birth_date: date, day: date) -> int:
    years = day.year - birth_date.year
    if (day.month, day.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


MISSING_BIRTH_DATE_WARNING = "fødselsdato mangler, sjekk alderen"


def already_on_shifts_warning(already: int) -> str | None:
    """The hint for someone who already has ``already`` other shifts that week."""
    return f"har allerede {already} skift denne uka" if already >= 1 else None


def age_problem(venue: str, birth_date: date | None, day: date) -> str | None:
    """Why the person is too young for this venue, or None.

    An unknown birth date is not a problem here; the app path asks for it.
    """
    minimum = VENUE_MIN_AGE.get(venue)
    if minimum is None or birth_date is None:
        return None
    if age_on(birth_date, day) < minimum:
        return f"{venue} krever {minimum} år"
    return None


def allowed_tokens(
    person: PersonEligibility,
    shift_group_ids: list[int],
    rules: Mapping[int, ShiftGroupRules] | None = None,
) -> list[str]:
    """Drag-and-drop tokens ``group:kind:open``, from the same rule as ``placement_problem``."""
    tokens: list[str] = []
    for group_id in shift_group_ids:
        group_rule = (rules or {}).get(group_id, NO_RULES)
        for kind in SLOT_KINDS:
            for open_for_samf in (False, True):
                if placement_problem(kind, group_id, open_for_samf, person, group_rule) is None:
                    tokens.append(f"{group_id}:{kind}:{int(open_for_samf)}")
    return tokens
