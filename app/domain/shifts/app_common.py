"""Shared context, helpers and internals for the volunteer shift app service.

Used by more than one area module (claim, mine, wishes, courses).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.shifts.app_models import (
    Audience,
    CourseOffer,
    CrewMember,
    OpenScope,
    Requirement,
)
from app.domain.shifts.app_repository import (
    GroupSetting,
    SessionRow,
    SignupRow,
)
from app.domain.shifts.app_rules import (
    STATUS_PUBLISHED,
    effective_week_status,
    semester_bounds,
    week_start,
)
from app.domain.shifts.errors import ShiftAccessError, ShiftNotFoundError
from app.domain.shifts.models import AssignedPerson, ShiftRecord, SlotRecord
from app.domain.shifts.permissions import participating_groups
from app.domain.shifts.planning import (
    EligibilityContext,
    OSLO,
    TRAINEE_ROLE,
    group_rules,
    person_eligibility,
    slot_kind,
)
from app.observability import get_logger
from app.shared.semester import get_current_semester_code

logger = get_logger("app.audit")

MAX_OPEN_DAYS = 92
COURSE_HORIZON_DAYS = 180


# ---------------------------------------------------------------- context


@dataclass(slots=True)
class _Person:
    volunteer_id: int
    first_name: str
    birth_date: date | None
    semester: int
    # Verv per shift group (groups with shift templates) this semester.
    roles_by_group: dict[int, set[str]]
    # Has any verv anywhere this semester (can do samfunnettjeneste).
    active: bool
    group_names: dict[int, str]
    settings: dict[int, GroupSetting]
    # Who may take what, from membership and qualification rows (not verv).
    # Built once per request; never read inside the per-slot evaluation.
    eligibility: EligibilityContext

    @property
    def enabled_ids(self) -> list[int]:
        return [gid for gid in self.group_names if self._setting(gid).app_enabled]

    @property
    def member_enabled_ids(self) -> list[int]:
        return [gid for gid in self.enabled_ids if gid in self.roles_by_group]

    @property
    def samf_ids(self) -> list[int]:
        return [gid for gid in self.enabled_ids if self._setting(gid).samf_enabled]

    @property
    def is_skjenke_member(self) -> bool:
        return bool(self.roles_by_group)

    @property
    def audience(self) -> Audience:
        if self.member_enabled_ids:
            return "skjenke"
        if self.active and self.samf_ids:
            return "samfunnettjeneste"
        return "none"

    def visible_group_ids(self) -> list[int]:
        if self.audience == "skjenke":
            return self.enabled_ids
        if self.audience == "samfunnettjeneste":
            return self.samf_ids
        return []

    def courses_enabled(self, group_id: int) -> bool:
        return self._setting(group_id).courses_enabled

    def meeting_point(self, group_id: int) -> str | None:
        return self._setting(group_id).meeting_point

    def _setting(self, group_id: int) -> GroupSetting:
        return self.settings.get(group_id) or GroupSetting(False, False, None)


@dataclass(slots=True)
class _Courses:
    ids: dict[str, int] = field(default_factory=dict)
    completions: dict[str, int] = field(default_factory=dict)
    signups: dict[str, SignupRow] = field(default_factory=dict)
    sessions: dict[str, list[SessionRow]] = field(default_factory=dict)

    def key_for_course(self, course_id: int) -> str | None:
        return next((key for key, cid in self.ids.items() if cid == course_id), None)


@dataclass(slots=True)
class _Mine:
    """The person's own shifts this semester, for quota, training and samf counts."""

    volunteer_id: int
    records: list[ShiftRecord]

    def entries(self) -> list[tuple[ShiftRecord, SlotRecord, AssignedPerson]]:
        return [
            (record, slot, person)
            for record in self.records
            for slot in record.slots
            for person in slot.people
            if person.volunteer_id == self.volunteer_id
        ]


@dataclass(slots=True)
class _Eval:
    hidden: bool = False
    block: str | None = None
    missing: list[str] = field(default_factory=list)
    offers: list[CourseOffer] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    course_ok_text: str | None = None
    training_text: str | None = None

    @property
    def can_claim(self) -> bool:
        return not self.hidden and self.block is None and not self.missing

    @property
    def can_combo(self) -> bool:
        return (
            not self.hidden
            and self.block is None
            and bool(self.missing)
            and len(self.offers) == len(self.missing)
        )



class ShiftsAppCommon:
    # ============================================================ internals

    async def _person_or_none(self, volunteer_id: int | None) -> _Person | None:
        if volunteer_id is None:
            return None
        row = await self.repository.get_person(volunteer_id)
        if row is None:
            return None
        semester = get_current_semester_code(self._now())
        role_rows = await self.repository.list_person_roles(volunteer_id, semester)
        group_options = await self.repository.list_shift_groups()
        group_names = {option.group_id: option.name for option in group_options}
        settings = await self.repository.list_group_settings()
        parents = await self.repository.list_group_parents()
        participating = participating_groups(
            await self.repository.list_direct_participating_group_ids(), parents
        )
        # A role in a participating group counts in the group itself and in its
        # direct children with templates (a parent-only member gets each bar
        # directly below). Deeper levels and siblings stay out.
        roles_by_group: dict[int, set[str]] = {}
        for group_id, role_name in role_rows:
            if group_id not in participating:
                continue
            children = {child for child, parent in parents.items() if parent == group_id}
            for target in ({group_id} | children) & group_names.keys():
                roles = roles_by_group.setdefault(target, set())
                if role_name:
                    roles.add(role_name)
        qualification_rows = [
            (record.qualification, record.status, record.group_id)
            for record in await self.repository.list_qualifications([volunteer_id])
        ]
        eligibility = EligibilityContext(
            person_eligibility(
                [group_id for group_id, _ in role_rows], qualification_rows, participating, parents
            ),
            group_rules(
                participating,
                parents,
                {gid: setting.sibling_skiftleder_allowed for gid, setting in settings.items()},
            ),
        )
        return _Person(
            volunteer_id=row.volunteer_id,
            first_name=row.first_name,
            birth_date=row.birth_date,
            semester=semester,
            roles_by_group=roles_by_group,
            active=bool(role_rows),
            group_names=group_names,
            settings=settings,
            eligibility=eligibility,
        )

    async def _require_person(self, volunteer_id: int) -> _Person:
        person = await self._person_or_none(volunteer_id)
        if person is None:
            raise ShiftAccessError("Skiftlista er ikke tilgjengelig for deg.")
        return person

    async def _require_audience(self, volunteer_id: int) -> _Person:
        person = await self._require_person(volunteer_id)
        if person.audience == "none":
            raise ShiftAccessError("Skiftlista er ikke slått på for deg ennå.")
        return person

    async def _require_wishes(self, volunteer_id: int) -> _Person:
        person = await self._require_audience(volunteer_id)
        if person.audience != "skjenke":
            raise ShiftAccessError("Ønsker er for de som har faste skift i en gruppe.")
        return person

    @staticmethod
    def _scope_groups(person: _Person, scope: OpenScope) -> list[int]:
        visible = person.visible_group_ids()
        if scope == "own":
            return [gid for gid in visible if gid in person.roles_by_group]
        if scope == "other":
            return [gid for gid in visible if gid not in person.roles_by_group]
        return visible

    async def _visible_slot(self, person: _Person, slot_id: int) -> tuple[ShiftRecord, SlotRecord]:
        shift_id = await self.repository.get_slot_shift_id(slot_id)
        record = await self.repository.get_shift(shift_id) if shift_id is not None else None
        slot = next((item for item in record.slots if item.slot_id == slot_id), None) if record else None
        if (
            record is None
            or slot is None
            or record.group_id not in person.visible_group_ids()
            or not await self._published_only([record])
        ):
            raise ShiftNotFoundError("Finner ikke plassen. Den kan ha blitt endret.")
        return record, slot

    async def _published_only(self, records: list[ShiftRecord]) -> list[ShiftRecord]:
        """Drop shifts in weeks that are still taking wishes or being allocated."""
        if not records:
            return []
        weeks = sorted({week_start(r.starts_at.astimezone(OSLO).date()) for r in records})
        statuses = await self.repository.week_statuses(sorted({r.group_id for r in records}), weeks)
        today = self._now().astimezone(OSLO).date()
        published: list[ShiftRecord] = []
        for record in records:
            week = week_start(record.starts_at.astimezone(OSLO).date())
            status = effective_week_status(statuses.get((record.group_id, week)), week, today)
            if status == STATUS_PUBLISHED:
                published.append(record)
        return published

    async def _mine(self, person: _Person) -> _Mine:
        start, _ = semester_bounds(person.semester)
        records = await self.repository.list_volunteer_shifts(person.volunteer_id, start)
        return _Mine(volunteer_id=person.volunteer_id, records=records)

    def _training_done(self, mine: _Mine, group_id: int) -> int:
        now = self._now()
        return sum(
            1
            for record, slot, _ in mine.entries()
            if record.group_id == group_id and slot.kind == "opplaering" and record.ends_at <= now
        )

    @staticmethod
    def _log(event: str, person: _Person, subject_id: int, *, subject_type: str = "shift_assignment") -> None:
        # Never log the free-text message: it is for the leaders only.
        logger.info(
            event,
            extra={
                "event": event,
                "volunteer_id": person.volunteer_id,
                "subject_type": subject_type,
                "subject_id": subject_id,
            },
        )


# ---------------------------------------------------------------- helpers


def _role_label(roles: set[str] | None) -> str:
    if not roles:
        return "Medlem"
    for preferred in ("Skiftleder", "Kokk", "Skjenker", TRAINEE_ROLE):
        if preferred in roles:
            return preferred
    return sorted(roles)[0]


def _crew(record: ShiftRecord) -> list[CrewMember]:
    """First names and places of the people coming. No last names, no "kan ikke"."""
    return [
        CrewMember(first_name=entry.first_name or "Ukjent", slot_label=slot_kind(slot.kind).label)
        for slot in sorted(record.slots, key=lambda item: (item.sort_order, item.slot_id))
        for entry in slot.people
        if not entry.cannot_attend
    ]


def _same_month(moment: datetime, local: datetime) -> bool:
    other = moment.astimezone(OSLO)
    return (other.year, other.month) == (local.year, local.month)


def _sentence(text: str) -> str:
    return text if text.endswith(".") else text + "."


def _expired_keys(evaluation: _Eval) -> set[str]:
    return {
        requirement.course_key
        for requirement in evaluation.requirements
        if requirement.course_key and "gikk ut" in requirement.text
    }
