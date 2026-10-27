"""Pure rules for Skiftlista in the app: courses, wishes, quota, labels.

Like ``planning.py`` these follow the prototype (mstene.no/skiftliste,
``logic.js``); keep them in step when they change. No database here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from app.domain.shifts.planning import BAR_KINDS, MONTHS_NO, OSLO, RESTRICTED_KINDS

# ---------------------------------------------------------------- wishes

WISH_ANSWERS: dict[str, str] = {
    "vil": "Vil gjerne",
    "kan": "Kan hvis nødvendig",
    "nei": "Kan ikke",
}

# Monthly allocation status. No row in shift_month_status = published.
MONTH_PUBLISHED = "published"
MONTH_STATUSES: dict[str, tuple[str, str]] = {
    "open": (
        "Åpen for ønsker",
        "Lederen fordeler måneden mellom 15. og 20. måneden før. Trykk på et skift for å si vil, kan eller nei.",
    ),
    "allocating": (
        "Fordeles nå",
        "Lederen holder på med fordelingen. Du kan justere ønskene til planen er publisert.",
    ),
    MONTH_PUBLISHED: ("Fordelt", "Planen er publisert. Skiftene dine ligger under Mine."),
}
WISHES_ACCEPTED = frozenset({"open", "allocating"})


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_label(month: date) -> str:
    return f"{MONTHS_NO[month.month - 1].capitalize()} {month.year}"


# ---------------------------------------------------------------- slot texts

# What the work is, shown on the place in the app. Generic on purpose;
# Personal can override per group later.
SLOT_DESCRIPTIONS: dict[str, str] = {
    "skiftleder": "Du leder skiftet, tar imot de som har samfunnettjeneste eller opplæring, og viser dem hva de skal gjøre.",
    "skjenker": "Bemanne kassen, servere mat og drikke, og rydde og vaske underveis.",
    "bartender": "Lage og servere drinker, ta bestillinger og holde orden i baren.",
    "kokk": "Lage maten som serveres i Stjernesalen.",
    "opplaering": "Du lærer rutinene sammen med en utlært. Etter 3 skift er du utlært.",
    "garderobe": "Ta imot og levere ut jakker og vesker.",
}
TRAINING_SHIFTS_NEEDED = 3

# ---------------------------------------------------------------- quota

PENSIONER_ROLE = "Pensjonist"
SAMF_TARGET = 2  # every volunteer takes 1–2 shifts a semester


@dataclass(frozen=True, slots=True)
class Quota:
    period: str  # "week" or "month"
    limit: int
    text: str


def quota_for(roles: set[str]) -> Quota:
    if PENSIONER_ROLE in roles:
        return Quota("month", 1, "Pensjonistavtale: 1 skift i måneden")
    return Quota("week", 1, "Kontrakt: 1 skift i uka")


def counts_as_samf(is_skjenke_member: bool, kind: str) -> bool:
    """A shift counts as samfunnettjeneste for someone outside Skjenke."""
    return not is_skjenke_member and kind not in RESTRICTED_KINDS


# ---------------------------------------------------------------- courses

COURSE_VALID_SEMESTERS = 4  # brann- and barkurs are valid 2 years


@dataclass(frozen=True, slots=True)
class CourseRule:
    key: str
    name: str
    # Names the course may have in Personal's course list (case-insensitive);
    # the first one that exists is used. A course that is not in Personal is
    # not checked at all, so add it there before relying on the rule.
    names_in_personal: tuple[str, ...]


FIRE_COURSE = CourseRule("brann", "Brannkurs", ("Brannkurs", "Brannvernkurs"))
BAR_COURSE = CourseRule("bar", "Barkurs", ("Barkurs",))
COURSE_RULES = {rule.key: rule for rule in (FIRE_COURSE, BAR_COURSE)}


def required_course_keys(kind: str, is_skjenke_member: bool) -> list[str]:
    """Brannkurs for everyone; barkurs for samfunnettjeneste in bar places."""
    keys = [FIRE_COURSE.key]
    if kind in BAR_KINDS and not is_skjenke_member:
        keys.append(BAR_COURSE.key)
    return keys


def semester_index(code: int) -> int:
    return (code // 10) * 2 + (code % 10) - 1


def semester_from_index(index: int) -> int:
    return (index // 2) * 10 + index % 2 + 1


def semester_of(moment: datetime) -> int:
    local = moment.astimezone(OSLO)
    return local.year * 10 + (1 if local.month <= 6 else 2)


def last_valid_semester(completed_semester: int) -> int:
    return semester_from_index(semester_index(completed_semester) + COURSE_VALID_SEMESTERS - 1)


def course_valid_in(completed_semester: int | None, semester: int) -> bool:
    if completed_semester is None:
        return False
    return semester_index(semester) <= semester_index(last_valid_semester(completed_semester))


def semester_label(code: int) -> str:
    return f"{'våren' if code % 10 == 1 else 'høsten'} {code // 10}"


def semester_bounds(code: int) -> tuple[datetime, datetime]:
    """Start and end of a semester (spring: Jan–Jun, autumn: Jul–Dec), Oslo time."""
    year = code // 10
    start = date(year, 1, 1) if code % 10 == 1 else date(year, 7, 1)
    end = date(year, 7, 1) if code % 10 == 1 else date(year + 1, 1, 1)
    return (
        datetime.combine(start, time.min, tzinfo=OSLO),
        datetime.combine(end, time.min, tzinfo=OSLO),
    )


MONTHS_SHORT_NO = ["jan", "feb", "mars", "april", "mai", "juni", "juli", "aug", "sep", "okt", "nov", "des"]


def short_date(moment: datetime) -> str:
    """"4. nov", like the prototype."""
    local = moment.astimezone(OSLO)
    return f"{local.day}. {MONTHS_SHORT_NO[local.month - 1]}"


def session_label(starts_at: datetime) -> str:
    local = starts_at.astimezone(OSLO)
    weekday = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"][local.weekday()]
    return f"{weekday} {local.day}. {MONTHS_NO[local.month - 1]} kl. {local:%H:%M}"


# ---------------------------------------------------------------- swaps and "kan ikke"

def cannot_attend_window_open(starts_at: datetime, ends_at: datetime, now: datetime) -> bool:
    """"Kan ikke i dag" is for the day itself: from midnight before the shift until it ends."""
    day_start = datetime.combine(starts_at.astimezone(OSLO).date(), time.min, tzinfo=OSLO)
    return day_start <= now < ends_at


def iso_week_key(moment: datetime) -> tuple[int, int]:
    calendar = moment.astimezone(OSLO).isocalendar()
    return calendar.year, calendar.week


def utc_now() -> datetime:
    return datetime.now(UTC)


def oslo_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=OSLO)
