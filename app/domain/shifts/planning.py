"""Pure planning rules for Skiftlista: no database, easy to test.

The same rules live in the clickable prototype (mstene.no/skiftliste,
``logic.js``) and in the app, so keep them in step when they change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.shifts.models import (
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


def open_places(shift: ShiftRecord, slot: SlotRecord) -> int:
    """How many more people this place can take.

    When opplæring takes a regular place, the crew place and the opplæring
    place share the crew count: a trainee fills one crew place, and a
    full crew leaves no room for a trainee.
    """
    free = slot.count - len(slot.people)
    crew = _crew_slot(shift.slots)
    training = _training_slot(shift.slots)
    if shift.training_replaces_slot and crew is not None and training is not None:
        if slot.slot_id in (crew.slot_id, training.slot_id):
            shared = crew.count - len(crew.people) - len(training.people)
            free = min(free, shared)
    return max(0, free)


def training_places_used(shift: ShiftRecord, slot: SlotRecord) -> int:
    """Crew places taken by trainees (shown as "Brukt til opplæring")."""
    crew = _crew_slot(shift.slots)
    training = _training_slot(shift.slots)
    if not shift.training_replaces_slot or crew is None or training is None:
        return 0
    if slot.slot_id != crew.slot_id:
        return 0
    return min(crew.count, len(training.people))


def build_shift_view(shift: ShiftRecord) -> ShiftView:
    slots: list[SlotView] = []
    total_open = 0
    for slot in sorted(shift.slots, key=lambda item: (item.sort_order, item.slot_id)):
        kind = slot_kind(slot.kind)
        free = 0 if shift.is_closed else open_places(shift, slot)
        used = training_places_used(shift, slot)
        if slot.kind != "opplaering":
            total_open += free
        if slot.kind == "opplaering":
            tag = "Tar en vanlig plass" if training_takes_place(shift) else "I tillegg"
        elif not slot.open_for_samf and not kind.tag.startswith("Kun"):
            tag = "Kun Skjenke"
        elif slot.open_for_samf:
            tag = "Åpen for samf."
        else:
            tag = kind.tag
        filled = len(slot.people) + used
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
                    and len(slot.people) < slot.count
                ),
                group_id=shift.group_id,
                open_for_samf=slot.open_for_samf,
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


# Verv names in Personal that give access to each kind of place.
SKIFTLEDER_ROLE = "Skiftleder"
TRAINEE_ROLE = "Opplæring"
CHEF_ROLE = "Kokk"


def placement_problem(
    kind: str,
    shift_group_id: int,
    open_for_samf: bool,
    roles_by_group: dict[int, set[str]],
) -> str | None:
    """Why this person cannot take this kind of place, or None if they can.

    ``roles_by_group`` holds the person's verv this semester in groups that
    have shifts (group id to verv names; a member without verv has an
    empty set).
    """
    own = roles_by_group.get(shift_group_id)
    member_anywhere = any(roles - {TRAINEE_ROLE} for roles in roles_by_group.values()) or any(
        not roles for roles in roles_by_group.values()
    )
    if kind == "skiftleder":
        return None if own and SKIFTLEDER_ROLE in own else "Kun for skiftledere i gruppen"
    if kind == "opplaering":
        return None if own and TRAINEE_ROLE in own else "Kun for de som er i opplæring i gruppen"
    if kind == "kokk":
        return None if own and CHEF_ROLE in own else "Kun for kokker"
    if own is not None and own == {TRAINEE_ROLE}:
        return "I opplæring: bruk opplæringsplassen"
    if member_anywhere or open_for_samf:
        return None
    return "Kun for Skjenke"


def allowed_tokens(roles_by_group: dict[int, set[str]], shift_group_ids: list[int]) -> list[str]:
    tokens: list[str] = []
    for group_id in shift_group_ids:
        for kind in SLOT_KINDS:
            for open_for_samf in (False, True):
                if placement_problem(kind, group_id, open_for_samf, roles_by_group) is None:
                    tokens.append(f"{group_id}:{kind}:{int(open_for_samf)}")
    return tokens
