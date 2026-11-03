"""Pure view model for the leader's week page (``GET /shifts/week``). No I/O.

``week_page.py`` loads the data and passes it in as a ``WeekInput``; this module turns it into
what the template draws and into the JSON block the page embeds for the next session's
JavaScript. The planning unit is the week and the page shows one bar at a time.

Grid rows are positions in the day: the 1st, 2nd, 3rd shift of the bar that day, in start-time
order. A row is labelled with the most common title and time range of that position across the
week (ties go to the earliest day). A day without a shift at that position is ``None``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.domain.shifts.models import AssignedPerson, DayBlock, ShiftView, SlotView
from app.domain.shifts.planning import WEEKDAYS_NO, monday_of
from app.domain.shifts.wish_calendar import month_label, week_month

MONTHS_SHORT = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"]
ANSWER_WORDS = {"vil": "vil", "kan": "kan", "nei": "kan ikke"}
NO_ANSWER_WORD = "ikke svart"
RED = "red"
YELLOW = "yellow"
# Board hints (``AssignedPerson.warnings``) that the grid already says in its own words.
_SKIPPED_WARNING_PREFIX = "har allerede"

STATUS_PUBLISHED_TEXT = "Publisert"
STATUS_DRAFT_TEXT = "Utkast: ikke synlig i appen"
STATUS_OPEN_TEXT = "Åpen for ønsker"


@dataclass(frozen=True, slots=True)
class MemberInput:
    """A member of the shown bar who is in the leader's pool."""

    volunteer_id: int
    name: str
    allowed_tokens: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WeekInput:
    bar_id: int
    bar_name: str
    week_start: date
    days: list[DayBlock]
    members: list[MemberInput]
    # (shift id, volunteer id) -> "vil" / "kan" / "nei"; missing means "ikke svart".
    answers: dict[tuple[int, int], str]
    # (volunteer id, Oslo day) -> shifts that day in every group, for the shown week.
    shifts_per_day: dict[tuple[int, date], int]
    # Shifts this month in every group (the month the week counts for).
    month_counts: dict[int, int]
    # Members with at least one answer on a shift of this bar in the shown week.
    began: frozenset[int]
    # "open" or "published" (what leaders see) for the shown week.
    status: str
    # Leader status of each week in the shown month, keyed by Monday.
    strip_statuses: dict[date, str]
    approved_skiftleder_token: str = ""
    # Person first (L2a): ``build_place_state`` output, embedded as is in the data block.
    place_state: dict = field(default_factory=dict)


@dataclass(slots=True)
class Problem:
    text: str
    level: str


@dataclass(slots=True)
class PlaceView:
    slot_id: int
    kind: str
    kind_label: str
    # "group:kind:open", the same token as ``allowed_tokens``.
    token: str
    is_open: bool
    volunteer_id: int | None = None
    assignment_id: int | None = None
    name: str = ""
    answer: str = ""
    answer_word: str = ""
    cannot_attend: bool = False
    swap_requested: bool = False
    problems: list[Problem] = field(default_factory=list)

    @property
    def level(self) -> str:
        levels = {problem.level for problem in self.problems}
        return RED if RED in levels else (YELLOW if YELLOW in levels else "")


@dataclass(slots=True)
class CellView:
    shift_id: int
    day: date
    title: str
    time_label: str
    is_closed: bool
    places: list[PlaceView]
    vil: int
    kan: int
    open_places: int
    # "tirsdag 3. nov.", for the texts of the place buttons.
    day_label: str = ""


@dataclass(slots=True)
class RowView:
    position: int
    label: str
    title: str
    time_label: str
    # Monday to Sunday; None where the bar has no shift at this position that day.
    cells: list[CellView | None]


@dataclass(slots=True)
class DayHead:
    day: date
    name: str
    date_label: str


@dataclass(slots=True)
class PersonRow:
    volunteer_id: int
    name: str
    is_sl: bool
    week_count: int
    month_count: int
    began: bool

    @property
    def status_word(self) -> str:
        return "har begynt" if self.began else "ikke svart"


@dataclass(slots=True)
class StripItem:
    week_start: date
    number: int
    status: str
    status_label: str
    is_shown: bool


@dataclass(slots=True)
class WeekView:
    bar_id: int
    bar_name: str
    week_start: date
    week_label: str
    previous_week: date
    next_week: date
    month_label: str
    status: str
    status_text: str
    heads: list[DayHead]
    rows: list[RowView]
    people: list[PersonRow]
    problem_count: int
    # Places with at least one red problem; the rest of ``problem_count`` are yellow-only warnings.
    problem_red_count: int
    problem_places: list[str]
    open_places: int
    without_shift: list[str]
    began_count: int
    member_count: int
    missing_names: list[str]
    strip: list[StripItem]
    data: dict


def short_date(day: date) -> str:
    return f"{day.day}. {MONTHS_SHORT[day.month - 1]}"


def day_label(day: date) -> str:
    """"tirsdag 3. nov."""
    return f"{WEEKDAYS_NO[day.weekday()].lower()} {short_date(day)}."


def week_label(week_start: date) -> str:
    """"Uke 45 (2.–8. nov)"; across a month border "Uke 49 (30. nov–6. des)"."""
    end = week_start + timedelta(days=6)
    number = week_start.isocalendar().week
    if week_start.month == end.month:
        span = f"{week_start.day}.–{end.day}. {MONTHS_SHORT[end.month - 1]}"
    else:
        span = f"{short_date(week_start)}–{short_date(end)}"
    return f"Uke {number} ({span})"


def status_text(status: str, has_placements: bool) -> str:
    """Published weeks are "Publisert". An open week with people placed holds drafts the app
    does not show; one without placements is just open for wishes."""
    if status == "published":
        return STATUS_PUBLISHED_TEXT
    return STATUS_DRAFT_TEXT if has_placements else STATUS_OPEN_TEXT


def month_weeks(month_first: date) -> list[date]:
    """Mondays of the weeks that count for the month (same rule as ``week_month``)."""
    monday = monday_of(month_first) - timedelta(days=7)
    found: list[date] = []
    for _ in range(8):
        if week_month(monday) == month_first:
            found.append(monday)
        monday += timedelta(days=7)
    return found


def _shifts_by_day(days: list[DayBlock]) -> dict[date, list[ShiftView]]:
    """The bar's shifts per day in start-time order. Venues are merged: the position is the
    position in the day within the bar, whatever room the shift is in."""
    found: dict[date, list[ShiftView]] = {}
    for block in days:
        found.setdefault(block.day, []).extend(shift for venue in block.venues for shift in venue.shifts)
    for shifts in found.values():
        # "HH:MM–HH:MM" sorts as start time within one day.
        shifts.sort(key=lambda item: (item.time_label, item.shift_id))
    return found


def _most_common(labels: list[tuple[str, str]]) -> tuple[str, str]:
    counts = Counter(labels)
    best = max(counts.values())
    return next(label for label in labels if counts[label] == best)


def _ordered_slots(slots: list[SlotView]) -> list[SlotView]:
    # The skiftleder place first, the others in their own order.
    return sorted(slots, key=lambda slot: slot.kind != "skiftleder")


def _problems(
    person: AssignedPerson,
    answer: str | None,
    day: date,
    *,
    day_count: int,
    week_count: int,
) -> list[Problem]:
    found: list[Problem] = []
    if answer == "nei":
        found.append(Problem("svarte kan ikke", RED))
    if day_count >= 2:
        found.append(Problem("to skift samme dag", RED))
    if person.cannot_attend:
        found.append(Problem("kan ikke i dag", RED))
    if week_count > 1:
        found.append(Problem(f"{week_count} skift denne uka", YELLOW))
    if person.swap_requested:
        found.append(Problem("ønsker bytte", YELLOW))
    found.extend(
        Problem(warning, YELLOW) for warning in person.warnings if not warning.startswith(_SKIPPED_WARNING_PREFIX)
    )
    return found


def _token(slot: SlotView) -> str:
    return f"{slot.group_id}:{slot.kind}:{int(slot.open_for_samf)}"


def _places(
    shift: ShiftView,
    day: date,
    data: WeekInput,
    member_ids: set[int],
    week_counts: dict[int, int],
) -> list[PlaceView]:
    places: list[PlaceView] = []
    for slot in _ordered_slots(shift.slots):
        for person in slot.people:
            answer = data.answers.get((shift.shift_id, person.volunteer_id)) if person.volunteer_id in member_ids else None
            places.append(
                PlaceView(
                    slot_id=slot.slot_id,
                    kind=slot.kind,
                    kind_label=slot.label,
                    token=_token(slot),
                    is_open=False,
                    volunteer_id=person.volunteer_id,
                    assignment_id=person.assignment_id,
                    name=person.name,
                    answer=answer or "",
                    # Only people in the leader's pool have their answers read.
                    answer_word=(ANSWER_WORDS.get(answer, NO_ANSWER_WORD) if person.volunteer_id in member_ids else ""),
                    cannot_attend=person.cannot_attend,
                    swap_requested=person.swap_requested,
                    problems=_problems(
                        person,
                        answer,
                        day,
                        day_count=data.shifts_per_day.get((person.volunteer_id, day), 0),
                        week_count=week_counts.get(person.volunteer_id, 0),
                    ),
                )
            )
        places.extend(
            PlaceView(slot.slot_id, slot.kind, slot.label, _token(slot), is_open=True)
            for _ in range(slot.open_places)
        )
    return places


def _cell(
    shift: ShiftView,
    day: date,
    data: WeekInput,
    member_ids: set[int],
    week_counts: dict[int, int],
) -> CellView:
    # "N vil · N kan" counts members of the shown bar only, which is the shift's own bar.
    answers = [data.answers.get((shift.shift_id, vid)) for vid in member_ids]
    return CellView(
        shift_id=shift.shift_id,
        day=day,
        title=shift.title,
        time_label=shift.time_label,
        is_closed=shift.is_closed,
        places=_places(shift, day, data, member_ids, week_counts),
        vil=answers.count("vil"),
        kan=answers.count("kan"),
        open_places=shift.open_places,
        day_label=day_label(day),
    )


def _week_counts(data: WeekInput) -> dict[int, int]:
    end = data.week_start + timedelta(days=7)
    totals: dict[int, int] = {}
    for (volunteer_id, day), count in data.shifts_per_day.items():
        if data.week_start <= day < end:
            totals[volunteer_id] = totals.get(volunteer_id, 0) + count
    return totals


def _rows(data: WeekInput, member_ids: set[int], week_counts: dict[int, int]) -> list[RowView]:
    by_day = _shifts_by_day(data.days)
    week_days = [data.week_start + timedelta(days=offset) for offset in range(7)]
    height = max((len(by_day.get(day, [])) for day in week_days), default=0)
    rows: list[RowView] = []
    for position in range(height):
        labels = [
            (by_day[day][position].title, by_day[day][position].time_label)
            for day in week_days
            if len(by_day.get(day, [])) > position
        ]
        title, time_label = _most_common(labels)
        cells = [
            _cell(by_day[day][position], day, data, member_ids, week_counts)
            if len(by_day.get(day, [])) > position
            else None
            for day in week_days
        ]
        rows.append(RowView(position + 1, f"{position + 1}. skift", title, time_label, cells))
    return rows


def _people(data: WeekInput, week_counts: dict[int, int]) -> list[PersonRow]:
    rows = [
        PersonRow(
            volunteer_id=member.volunteer_id,
            name=member.name,
            is_sl=data.approved_skiftleder_token in member.allowed_tokens,
            week_count=week_counts.get(member.volunteer_id, 0),
            month_count=data.month_counts.get(member.volunteer_id, 0),
            began=member.volunteer_id in data.began,
        )
        for member in data.members
    ]
    # Fewest shifts first, like the prototype; the next session adds client side sorting.
    rows.sort(key=lambda row: (row.week_count, row.month_count, row.name.casefold(), row.volunteer_id))
    return rows


def _strip(data: WeekInput) -> list[StripItem]:
    month = week_month(data.week_start)
    items: list[StripItem] = []
    for monday in month_weeks(month):
        status = data.strip_statuses.get(monday, "open")
        items.append(
            StripItem(
                week_start=monday,
                number=monday.isocalendar().week,
                status=status,
                status_label=STATUS_PUBLISHED_TEXT if status == "published" else STATUS_OPEN_TEXT,
                is_shown=monday == data.week_start,
            )
        )
    return items


def week_data_block(data: WeekInput, rows: list[RowView], people: list[PersonRow]) -> dict:
    """What the page embeds as ``<script type="application/json" id="week-data">``.

    Per shift: its places and the answer of each member who answered (a missing id means "ikke
    svart"). Per person: SL mark, shifts this week and month, days with a shift this week and the
    place tokens (``group:kind:open``) the person may take. Names are the leader's pool only.
    """
    week_end = data.week_start + timedelta(days=7)
    busy: dict[int, set[str]] = {}
    for (volunteer_id, day), count in data.shifts_per_day.items():
        if count and data.week_start <= day < week_end:
            busy.setdefault(volunteer_id, set()).add(day.isoformat())
    member_ids = {member.volunteer_id for member in data.members}
    shifts = []
    for row in rows:
        for cell in row.cells:
            if cell is None:
                continue
            shifts.append(
                {
                    "shift_id": cell.shift_id,
                    "day": cell.day.isoformat(),
                    "position": row.position,
                    "title": cell.title,
                    "time": cell.time_label,
                    "closed": cell.is_closed,
                    "places": [
                        {
                            "slot_id": place.slot_id,
                            "kind": place.kind,
                            "token": place.token,
                            "assignment_id": place.assignment_id,
                            "volunteer_id": place.volunteer_id,
                        }
                        for place in cell.places
                    ],
                    "answers": {
                        str(vid): answer
                        for vid in sorted(member_ids)
                        if (answer := data.answers.get((cell.shift_id, vid)))
                    },
                }
            )
    by_id = {member.volunteer_id: member for member in data.members}
    return {
        "version": 1,
        "bar_id": data.bar_id,
        "week_start": data.week_start.isoformat(),
        "shifts": shifts,
        "place_state": data.place_state,
        "people": [
            {
                "volunteer_id": person.volunteer_id,
                "name": person.name,
                "sl": person.is_sl,
                "week_shifts": person.week_count,
                "month_shifts": person.month_count,
                "began": person.began,
                "busy_days": sorted(busy.get(person.volunteer_id, ())),
                "allowed": list(by_id[person.volunteer_id].allowed_tokens),
            }
            for person in people
        ],
    }


def build_week_view(data: WeekInput) -> WeekView:
    member_ids = {member.volunteer_id for member in data.members}
    week_counts = _week_counts(data)
    rows = _rows(data, member_ids, week_counts)
    people = _people(data, week_counts)
    places = [place for row in rows for cell in row.cells if cell for place in cell.places]
    problem_places = [
        f"{cell.day.day}. {MONTHS_SHORT[cell.day.month - 1]}, {row.label}: {place.name}"
        for row in rows
        for cell in row.cells
        if cell
        for place in cell.places
        if place.problems
    ]
    without_shift = [person.name for person in people if person.began and person.week_count == 0]
    missing = sorted((person.name for person in people if not person.began), key=str.casefold)
    month = week_month(data.week_start)
    return WeekView(
        bar_id=data.bar_id,
        bar_name=data.bar_name,
        week_start=data.week_start,
        week_label=week_label(data.week_start),
        previous_week=data.week_start - timedelta(days=7),
        next_week=data.week_start + timedelta(days=7),
        month_label=month_label(month),
        status=data.status,
        status_text=status_text(data.status, any(not place.is_open for place in places)),
        heads=[
            DayHead(
                day=day,
                name=WEEKDAYS_NO[offset],
                date_label=short_date(day),
            )
            for offset in range(7)
            for day in [data.week_start + timedelta(days=offset)]
        ],
        rows=rows,
        people=people,
        problem_count=len(problem_places),
        problem_red_count=sum(1 for place in places if place.level == RED),
        problem_places=problem_places,
        open_places=sum(cell.open_places for row in rows for cell in row.cells if cell),
        without_shift=without_shift,
        began_count=sum(1 for person in people if person.began),
        member_count=len(people),
        missing_names=missing,
        strip=_strip(data),
        data=week_data_block(data, rows, people),
    )

