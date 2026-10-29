"""Skiftlista for volunteers in the app (kvarteret-internbevis).

Leaders plan in Personal (``ShiftsService``); volunteers use these calls
from the app: what is switched on, open places, their own shifts, taking
a place, swap and "kan ikke i dag", wishes and course sign-up.

Taking a place goes through the same checks as the leader board
(``ShiftsService._check``) plus what only applies in the app: age,
brann-/barkurs, and only the places the person may see.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from app.domain.shifts.app_models import (
    Audience,
    ClaimPlaceRequest,
    CourseOffer,
    CourseSessionOut,
    CourseStatus,
    CrewMember,
    LabeledValue,
    MyShift,
    MyShiftsResponse,
    OpenPlace,
    OpenScope,
    OpenShift,
    OpenShiftsResponse,
    PlaceDetailResponse,
    QuotaSummary,
    Requirement,
    SamfProgress,
    ShiftActionResponse,
    ShiftAppConfigResponse,
    ShiftAppGroup,
    ShiftAppTab,
    ShiftCoursesResponse,
    ShiftSlotKind,
    ShiftSummary,
    ShiftWishesResponse,
    WeekdayRule,
    WishWeek,
    WishShift,
)
from app.domain.shifts.app_repository import (
    GroupSetting,
    SessionRow,
    ShiftsAppRepository,
    SignupRow,
)
from app.domain.shifts.app_rules import (
    BAR_COURSE,
    COURSE_RULES,
    FIRE_COURSE,
    PERIOD_STATUSES,
    STATUS_PUBLISHED,
    SAMF_TARGET,
    SLOT_DESCRIPTIONS,
    TRAINING_SHIFTS_NEEDED,
    WISH_ANSWERS,
    WISHES_ACCEPTED,
    cannot_attend_window_open,
    counts_as_samf,
    course_valid_in,
    effective_week_status,
    iso_week_key,
    last_valid_semester,
    oslo_midnight,
    quota_for,
    required_course_keys,
    semester_bounds,
    semester_label,
    semester_of,
    session_label,
    short_date,
    week_contract_warning,
    utc_now,
    week_label,
    week_start,
    effective_answer,
    wish_window_end,
)
from app.domain.shifts.errors import ShiftAccessError, ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import AssignedPerson, ShiftRecord, SlotRecord
from app.domain.shifts.permissions import participating_groups
from app.domain.shifts.planning import (
    MONTHS_NO,
    EligibilityContext,
    OSLO,
    RESTRICTED_KINDS,
    SLOT_KINDS,
    TRAINEE_ROLE,
    VENUE_LABELS,
    VENUE_MIN_AGE,
    WEEKDAYS_NO,
    age_on,
    format_day,
    format_time_range,
    group_rules,
    open_for_everyone,
    open_places,
    opened_by_absence,
    person_eligibility,
    slot_kind,
)
from app.domain.shifts.service import ShiftsService
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


class ShiftsAppService:
    def __init__(
        self,
        repository: ShiftsAppRepository,
        shifts_service: ShiftsService | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.shifts = shifts_service or ShiftsService(repository)
        self._now = now

    # ------------------------------------------------------------ config

    async def get_config(self, volunteer_id: int | None) -> ShiftAppConfigResponse:
        person = await self._person_or_none(volunteer_id)
        audience: Audience = person.audience if person else "none"
        groups: list[ShiftAppGroup] = []
        tabs: list[ShiftAppTab] = []
        if person is not None and audience != "none":
            mine = await self._mine(person)
            for group_id in person.visible_group_ids():
                roles = person.roles_by_group.get(group_id)
                is_member = roles is not None
                in_training = bool(roles) and TRAINEE_ROLE in (roles or set())
                groups.append(
                    ShiftAppGroup(
                        group_id=group_id,
                        name=person.group_names[group_id],
                        is_member=is_member,
                        role=_role_label(roles) if is_member else None,
                        in_training=in_training,
                        training_done=self._training_done(mine, group_id) if in_training else None,
                        training_needed=TRAINING_SHIFTS_NEEDED if in_training else None,
                        meeting_point=person.meeting_point(group_id),
                        quota_text=quota_for(roles or set()).text if is_member else None,
                    )
                )
            if audience == "skjenke":
                own = person.member_enabled_ids
                own_label = person.group_names[own[0]] if len(own) == 1 else "Mine grupper"
                tabs = [ShiftAppTab(id="wishes", label="Ønsker"), ShiftAppTab(id="open", label=own_label, scope="own")]
                if len(person.enabled_ids) > len(own):
                    tabs.append(ShiftAppTab(id="open", label="Andre", scope="other"))
                tabs.append(ShiftAppTab(id="mine", label="Mine"))
            else:
                tabs = [
                    ShiftAppTab(id="open", label="Ledige skift", scope="all"),
                    ShiftAppTab(id="mine", label="Mine skift"),
                ]
        return ShiftAppConfigResponse(
            enabled=audience != "none",
            audience=audience,
            tabs=tabs,
            groups=groups,
            slot_kinds=[
                ShiftSlotKind(
                    kind=kind,
                    label=info.label,
                    crew=info.crew,
                    restricted=kind in RESTRICTED_KINDS,
                    description=SLOT_DESCRIPTIONS.get(kind),
                )
                for kind, info in SLOT_KINDS.items()
            ],
            wish_answers=[LabeledValue(value=value, label=label) for value, label in WISH_ANSWERS.items()],
            weekdays=list(WEEKDAYS_NO),
            courses=[LabeledValue(value=rule.key, label=rule.name) for rule in COURSE_RULES.values()],
            samf_target=SAMF_TARGET,
        )

    # ------------------------------------------------------------ open places

    async def list_open(self, volunteer_id: int, *, scope: OpenScope, days: int) -> OpenShiftsResponse:
        person = await self._require_audience(volunteer_id)
        now = self._now()
        days = max(1, min(days, MAX_OPEN_DAYS))
        today = now.astimezone(OSLO).date()
        until = oslo_midnight(today + timedelta(days=days + 1))
        furthest = oslo_midnight(today + timedelta(days=MAX_OPEN_DAYS + 1))
        group_ids = self._scope_groups(person, scope)
        records = await self.repository.list_shifts_between(now, furthest, group_ids=group_ids)
        records = await self._published_only(records)
        courses = await self._courses(person)
        mine = await self._mine(person)
        result: list[OpenShift] = []
        has_more = False
        for record in records:
            if has_more and record.starts_at >= until:
                break
            if any(p.volunteer_id == person.volunteer_id for s in record.slots for p in s.people):
                continue
            places = []
            for slot in sorted(record.slots, key=lambda item: (item.sort_order, item.slot_id)):
                evaluation = self._evaluate(person, record, slot, courses, mine)
                if not evaluation.hidden:
                    places.append(self._open_place(person, record, slot, evaluation))
            if not places:
                continue
            if record.starts_at >= until:
                has_more = True
            else:
                result.append(OpenShift(**self._summary(record).model_dump(), places=places))
        return OpenShiftsResponse(
            scope=scope,
            from_day=today,
            to_day=today + timedelta(days=days),
            shifts=result,
            has_more=has_more,
        )

    async def get_place(self, volunteer_id: int, slot_id: int) -> PlaceDetailResponse:
        person = await self._require_audience(volunteer_id)
        record, slot = await self._visible_slot(person, slot_id)
        courses = await self._courses(person)
        mine = await self._mine(person)
        evaluation = self._evaluate(person, record, slot, courses, mine, detail=True)
        claim_label = None
        if evaluation.can_claim:
            claim_label = "Ta skiftet"
        elif evaluation.can_combo:
            names = " og ".join(
                f"{offer.course_name.lower()} {short_date(offer.starts_at)}" for offer in evaluation.offers
            )
            claim_label = f"Meld meg på {names}, og ta skiftet"
        return PlaceDetailResponse(
            shift=self._summary(record),
            place=self._open_place(person, record, slot, evaluation),
            description=SLOT_DESCRIPTIONS.get(slot.kind),
            meeting_point=person.meeting_point(record.group_id),
            requirements=evaluation.requirements,
            crew=_crew(record),
            claim_label=claim_label,
        )

    async def claim(
        self, volunteer_id: int, slot_id: int, request: ClaimPlaceRequest
    ) -> ShiftActionResponse:
        person = await self._require_audience(volunteer_id)
        record, _ = await self._visible_slot(person, slot_id)
        await self.repository.lock_shift(record.shift_id)
        # Read again under the lock: someone may have taken the place meanwhile.
        record, slot = await self._visible_slot(person, slot_id)
        courses = await self._courses(person)
        mine = await self._mine(person)

        requested = list(dict.fromkeys(request.course_session_ids))
        planned, signing_up = self._planned_signups(courses, record, requested)
        evaluation = self._evaluate(person, record, slot, planned, mine, detail=True)
        if evaluation.hidden or evaluation.block:
            raise ShiftPlacementError(_sentence(evaluation.block or "Du kan ikke ta denne plassen"))
        if evaluation.missing:
            first = evaluation.missing[0]
            raise ShiftPlacementError(
                f"Du må være påmeldt {COURSE_RULES[first].name.lower()} før skiftet. "
                "Velg et kurs og prøv igjen."
            )
        await self.shifts._check(
            record, slot, person.volunteer_id, eligibility=person.eligibility
        )

        for session_id in requested:
            await self._sign_up(person, courses, session_id, before=record.starts_at)
        await self.shifts._take_place(record, slot)
        await self.repository.add_assignment(slot.slot_id, person.volunteer_id)
        logger.info(
            "shift.app.claim",
            extra={
                "event": "shift.app.claim",
                "volunteer_id": person.volunteer_id,
                "subject_type": "shift_slot",
                "subject_id": slot.slot_id,
                "course_sessions": requested,
            },
        )
        signed = [COURSE_RULES[key].name.lower() for key in signing_up]
        message = (
            f"Du er påmeldt {' og '.join(signed)} og satt opp på skiftet."
            if signed
            else "Du er satt opp. Skiftet ligger under Mine."
        )
        return ShiftActionResponse(message=message, shift=await self._my_shift(person, slot.slot_id))

    # ------------------------------------------------------------ my shifts

    async def list_mine(self, volunteer_id: int) -> MyShiftsResponse:
        person = await self._require_person(volunteer_id)
        now = self._now()
        mine = await self._mine(person)
        published = await self._published_only(mine.records)
        published_ids = {record.shift_id for record in published}
        warnings = self._week_warnings(person, published)
        shifts = [
            self._to_my_shift(person, record, slot, entry, warnings.get(record.shift_id, []))
            for record, slot, entry in mine.entries()
            if record.shift_id in published_ids and record.ends_at > now
        ]
        quota = None
        samf = None
        entries = [item for item in mine.entries() if item[0].shift_id in published_ids]
        if person.member_enabled_ids:
            roles = set().union(*(person.roles_by_group[gid] for gid in person.member_enabled_ids))
            rule = quota_for(roles)
            local = now.astimezone(OSLO)
            if rule.period == "month":
                taken = sum(1 for record, _, _ in entries if _same_month(record.starts_at, local))
                label = MONTHS_NO[local.month - 1].capitalize()
            else:
                week = iso_week_key(now)
                taken = sum(1 for record, _, _ in entries if iso_week_key(record.starts_at) == week)
                label = f"Denne uka (uke {week[1]})"
            quota = QuotaSummary(label=label, taken=taken, limit=rule.limit, text=rule.text)
        elif not person.is_skjenke_member:
            counted = [
                record for record, slot, _ in entries if counts_as_samf(False, slot.kind)
            ]
            done = sum(1 for record in counted if record.ends_at <= now)
            planned = len(counted) - done
            text = (
                f"{done} gjennomført, {planned} planlagt. Takk! Alle tar 1–2 skift i semesteret."
                if counted
                else "Alle frivillige tar 1–2 skift i semesteret. Du kan melde deg på for hele semesteret."
            )
            samf = SamfProgress(done=done, planned=planned, target=SAMF_TARGET, text=text)
        return MyShiftsResponse(shifts=shifts, quota=quota, samfunnettjeneste=samf)

    async def request_swap(self, volunteer_id: int, assignment_id: int, message: str | None) -> ShiftActionResponse:
        person, state, record = await self._own_assignment(volunteer_id, assignment_id)
        if record.starts_at <= self._now():
            raise ShiftPlacementError("Skiftet har startet. Bruk «Kan ikke i dag» hvis du ikke kan komme.")
        if state.cannot_attend:
            raise ShiftPlacementError("Du har allerede sagt at du ikke kan komme.")
        await self._set_status(state.assignment_id, swap=True, cannot=False, message=message)
        self._log("shift.app.swap_requested", person, state.assignment_id)
        return ShiftActionResponse(
            message=(
                "Markert som ønsker bytte. Lederne får ikke varsel ennå, så si også fra til skiftlederen. "
                "Du står på skiftet til noen tar over."
            ),
            shift=await self._my_shift(person, state.slot_id),
        )

    async def cancel_swap(self, volunteer_id: int, assignment_id: int) -> ShiftActionResponse:
        person, state, record = await self._own_assignment(volunteer_id, assignment_id)
        if not state.swap_requested:
            raise ShiftPlacementError("Du har ikke bedt om bytte på dette skiftet.")
        await self._set_status(state.assignment_id, swap=False, cannot=state.cannot_attend, message=None)
        self._log("shift.app.swap_cancelled", person, state.assignment_id)
        return ShiftActionResponse(
            message="Byttet er avlyst. Du står fortsatt på skiftet.",
            shift=await self._my_shift(person, state.slot_id),
        )

    async def report_cannot_attend(
        self, volunteer_id: int, assignment_id: int, message: str | None
    ) -> ShiftActionResponse:
        person, state, record = await self._own_assignment(volunteer_id, assignment_id)
        if not cannot_attend_window_open(record.starts_at, record.ends_at, self._now()):
            raise ShiftPlacementError(
                "«Kan ikke i dag» er for selve dagen. Før det: be om bytte."
            )
        if state.cannot_attend:
            raise ShiftPlacementError("Du har allerede sagt at du ikke kan komme.")
        await self._set_status(state.assignment_id, swap=False, cannot=True, message=message)
        self._log("shift.app.cannot_attend", person, state.assignment_id)
        return ShiftActionResponse(
            message=(
                "Markert. Lederne får ikke varsel ennå, så si også fra til skiftlederen. "
                "Plassen er åpen for andre, og du går av skiftet når noen tar den."
            ),
            shift=await self._my_shift(person, state.slot_id),
        )

    async def undo_cannot_attend(self, volunteer_id: int, assignment_id: int) -> ShiftActionResponse:
        person, state, record = await self._own_assignment(volunteer_id, assignment_id)
        if not state.cannot_attend:
            raise ShiftPlacementError("Du står på skiftet som vanlig.")
        await self._set_status(state.assignment_id, swap=False, cannot=False, message=None)
        self._log("shift.app.cannot_attend_undone", person, state.assignment_id)
        return ShiftActionResponse(
            message="Fint! Du står på skiftet igjen.",
            shift=await self._my_shift(person, state.slot_id),
        )

    # ------------------------------------------------------------ wishes

    async def get_wishes(self, volunteer_id: int) -> ShiftWishesResponse:
        person = await self._require_wishes(volunteer_id)
        now = self._now()
        group_ids = person.member_enabled_ids
        weekdays = await self.repository.list_template_weekdays(group_ids)
        rules = await self.repository.list_rules(person.volunteer_id, person.semester)
        today = now.astimezone(OSLO).date()
        records = await self.repository.list_shifts_between(
            now, oslo_midnight(wish_window_end(today) + timedelta(days=1)), group_ids=sorted(group_ids)
        )
        by_week: dict[tuple[int, date], list[ShiftRecord]] = {}
        for record in records:
            week = week_start(record.starts_at.astimezone(OSLO).date())
            by_week.setdefault((record.group_id, week), []).append(record)
        statuses = await self.repository.week_statuses(
            sorted(group_ids), sorted({week for _, week in by_week})
        )

        week_rows: list[WishWeek] = []
        accepting: set[tuple[int, date]] = set()
        for group_id, week in sorted(by_week, key=lambda key: (key[1], person.group_names[key[0]])):
            status = effective_week_status(statuses.get((group_id, week)), week, today)
            label, note = PERIOD_STATUSES.get(status, PERIOD_STATUSES[STATUS_PUBLISHED])
            accepts = status in WISHES_ACCEPTED
            if accepts:
                accepting.add((group_id, week))
            week_rows.append(
                WishWeek(
                    group_id=group_id,
                    group_name=person.group_names[group_id],
                    week_start=week,
                    week_number=week.isocalendar().week,
                    label=week_label(week),
                    status=status,  # type: ignore[arg-type]
                    status_label=label,
                    note=note,
                    accepts_wishes=accepts,
                )
            )

        shift_rows: list[WishShift] = []
        records = [
            record
            for record in records
            if (record.group_id, week_start(record.starts_at.astimezone(OSLO).date())) in accepting
        ]
        if records:
            wishes = await self.repository.list_wishes(
                person.volunteer_id, [record.shift_id for record in records]
            )
            for record in records:
                weekday = record.starts_at.astimezone(OSLO).weekday()
                explicit = wishes.get(record.shift_id)
                shift_rows.append(
                    WishShift(
                        **self._summary(record).model_dump(),
                        answer=effective_answer(explicit, rules.get(weekday)),  # type: ignore[arg-type]
                        from_rule=explicit is None,
                    )
                )
        return ShiftWishesResponse(
            semester=person.semester,
            semester_label=semester_label(person.semester).capitalize(),
            rules=[
                WeekdayRule(weekday=day, label=WEEKDAYS_NO[day], answer=rules.get(day))  # type: ignore[arg-type]
                for day in sorted(weekdays | set(rules))
            ],
            weeks=week_rows,
            shifts=shift_rows,
        )

    async def set_weekday_rule(self, volunteer_id: int, weekday: int, answer: str | None) -> ShiftWishesResponse:
        person = await self._require_wishes(volunteer_id)
        await self.repository.set_rule(person.volunteer_id, person.semester, weekday, answer)
        return await self.get_wishes(volunteer_id)

    async def set_shift_wish(self, volunteer_id: int, shift_id: int, answer: str | None) -> ShiftWishesResponse:
        person = await self._require_wishes(volunteer_id)
        record = await self.repository.get_shift(shift_id)
        if record is None or record.group_id not in person.member_enabled_ids:
            raise ShiftNotFoundError("Finner ikke skiftet.")
        if record.starts_at <= self._now():
            raise ShiftPlacementError("Skiftet har allerede vært.")
        week = week_start(record.starts_at.astimezone(OSLO).date())
        statuses = await self.repository.week_statuses([record.group_id], [week])
        today = self._now().astimezone(OSLO).date()
        if effective_week_status(statuses.get((record.group_id, week)), week, today) not in WISHES_ACCEPTED:
            raise ShiftPlacementError("Planen for denne uka er publisert. Be om bytte i stedet.")
        await self.repository.set_wish(person.volunteer_id, shift_id, answer)
        return await self.get_wishes(volunteer_id)

    # ------------------------------------------------------------ courses

    async def get_courses(self, volunteer_id: int) -> ShiftCoursesResponse:
        person = await self._require_audience(volunteer_id)
        courses = await self._courses(person)
        rows: list[CourseStatus] = []
        for key in self._course_keys(person):
            if key not in courses.ids:
                continue
            rows.append(self._course_status(person, courses, key))
        return ShiftCoursesResponse(courses=rows)

    async def sign_up(self, volunteer_id: int, session_id: int) -> ShiftCoursesResponse:
        person = await self._require_audience(volunteer_id)
        courses = await self._courses(person)
        await self._sign_up(person, courses, session_id, before=None)
        self._log("course.app.signup", person, session_id, subject_type="course_session")
        return await self.get_courses(volunteer_id)

    async def leave_session(self, volunteer_id: int, session_id: int) -> ShiftCoursesResponse:
        person = await self._require_audience(volunteer_id)
        courses = await self._courses(person)
        key = next((k for k, row in courses.signups.items() if row.session_id == session_id), None)
        if key is None:
            raise ShiftNotFoundError("Du er ikke påmeldt dette kurset.")
        needed = await self._shift_needing_signup(person, courses, key)
        if needed is not None:
            raise ShiftPlacementError(
                f"Du trenger {COURSE_RULES[key].name.lower()} før skiftet {format_day(needed.starts_at.astimezone(OSLO).date()).lower()}. "
                "Velg en annen dato før skiftet i stedet, eller be om bytte."
            )
        await self.repository.remove_signup(session_id, person.volunteer_id)
        self._log("course.app.signup_cancelled", person, session_id, subject_type="course_session")
        return await self.get_courses(volunteer_id)

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
        roles_by_group: dict[int, set[str]] = {}
        for group_id, role_name in role_rows:
            if group_id in group_names:
                roles = roles_by_group.setdefault(group_id, set())
                if role_name:
                    roles.add(role_name)
        settings = await self.repository.list_group_settings()
        parents = await self.repository.list_group_parents()
        participating = participating_groups(
            await self.repository.list_direct_participating_group_ids(), parents
        )
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

    def _course_keys(self, person: _Person) -> list[str]:
        keys = [FIRE_COURSE.key]
        if not person.is_skjenke_member:
            keys.append(BAR_COURSE.key)
        return keys

    async def _courses(self, person: _Person) -> _Courses:
        now = self._now()
        found = await self.repository.find_courses(
            [name for rule in COURSE_RULES.values() for name in rule.names_in_personal]
        )
        ids: dict[str, int] = {}
        for key, rule in COURSE_RULES.items():
            course_id = next((found[n.lower()] for n in rule.names_in_personal if n.lower() in found), None)
            if course_id is not None:
                ids[key] = course_id
        if not ids:
            return _Courses()
        by_id = {course_id: key for key, course_id in ids.items()}
        completions = await self.repository.latest_completions(person.volunteer_id, list(ids.values()))
        signups: dict[str, SignupRow] = {}
        for row in await self.repository.list_signups(person.volunteer_id, list(ids.values()), now):
            signups.setdefault(by_id[row.course_id], row)
        sessions: dict[str, list[SessionRow]] = {key: [] for key in ids}
        for row in await self.repository.list_sessions(
            list(ids.values()), now, now + timedelta(days=COURSE_HORIZON_DAYS)
        ):
            sessions[by_id[row.course_id]].append(row)
        return _Courses(
            ids=ids,
            completions={by_id[cid]: semester for cid, semester in completions.items()},
            signups=signups,
            sessions=sessions,
        )

    def _planned_signups(
        self, courses: _Courses, record: ShiftRecord, session_ids: list[int]
    ) -> tuple[_Courses, list[str]]:
        """The course picture if the requested sessions were booked, and their course keys."""
        signups = dict(courses.signups)
        keys: list[str] = []
        for session_id in session_ids:
            found = next(
                (
                    (key, row)
                    for key, rows in courses.sessions.items()
                    for row in rows
                    if row.session_id == session_id
                ),
                None,
            )
            if found is None:
                raise ShiftNotFoundError("Finner ikke kurset. Det kan ha blitt endret.")
            key, row = found
            if row.starts_at >= record.starts_at:
                raise ShiftPlacementError("Kurset må være før skiftet.")
            already = courses.signups.get(key)
            if row.spots_left <= 0 and (already is None or already.session_id != session_id):
                raise ShiftPlacementError("Kurset er fullt. Velg en annen dato.")
            signups[key] = SignupRow(row.session_id, row.course_id, row.starts_at)
            if key not in keys:
                keys.append(key)
        return replace(courses, signups=signups), keys

    async def _sign_up(
        self, person: _Person, courses: _Courses, session_id: int, *, before: datetime | None
    ) -> None:
        session = await self.repository.get_session(session_id)
        key = courses.key_for_course(session.course_id) if session else None
        if (
            session is None
            or key is None
            or key not in self._course_keys(person)
            or session.is_cancelled
            or session.starts_at <= self._now()
        ):
            raise ShiftNotFoundError("Finner ikke kurset. Det kan ha blitt endret.")
        existing = courses.signups.get(key)
        if existing is not None and existing.session_id == session_id:
            return
        await self.repository.lock_session(session_id)
        session = await self.repository.get_session(session_id)
        if session is None or session.spots_left <= 0:
            raise ShiftPlacementError("Kurset er fullt. Velg en annen dato.")
        if before is not None and session.starts_at >= before:
            raise ShiftPlacementError("Kurset må være før skiftet.")
        if existing is not None:
            needed = await self._shift_needing_signup(person, courses, key)
            if needed is not None and session.starts_at >= needed.starts_at:
                raise ShiftPlacementError(
                    f"Du trenger {COURSE_RULES[key].name.lower()} før skiftet "
                    f"{format_day(needed.starts_at.astimezone(OSLO).date()).lower()}. Velg en dato før det."
                )
            await self.repository.remove_signup(existing.session_id, person.volunteer_id)
        await self.repository.add_signup(session_id, person.volunteer_id)
        courses.signups[key] = SignupRow(session.session_id, session.course_id, session.starts_at)

    async def _shift_needing_signup(self, person: _Person, courses: _Courses, key: str) -> ShiftRecord | None:
        """The first coming shift that is only allowed because of this sign-up."""
        signup = courses.signups.get(key)
        if signup is None:
            return None
        now = self._now()
        mine = await self._mine(person)
        for record, slot, _ in mine.entries():
            if record.starts_at <= now or record.starts_at <= signup.starts_at:
                continue
            if not person.courses_enabled(record.group_id):
                continue
            if key not in required_course_keys(slot.kind, person.is_skjenke_member):
                continue
            if course_valid_in(courses.completions.get(key), semester_of(record.starts_at)):
                continue
            return record
        return None

    def _course_status(self, person: _Person, courses: _Courses, key: str) -> CourseStatus:
        rule = COURSE_RULES[key]
        completed = courses.completions.get(key)
        valid = course_valid_in(completed, person.semester)
        through = semester_label(last_valid_semester(completed)) if completed else None
        expires_soon = valid and completed is not None and last_valid_semester(completed) == person.semester
        signup = courses.signups.get(key)
        sessions = [
            CourseSessionOut(
                session_id=row.session_id,
                starts_at=row.starts_at,
                label=session_label(row.starts_at).capitalize(),
                location=row.location,
                spots_left=row.spots_left,
                signed_up=signup is not None and signup.session_id == row.session_id,
            )
            for row in courses.sessions.get(key, [])
        ]
        signed = next((item for item in sessions if item.signed_up), None)
        upcoming = next((item for item in sessions if item.spots_left > 0), None)
        next_text = f" Neste kurs {upcoming.label[0].lower() + upcoming.label[1:]}." if upcoming else " Ingen kurs satt opp ennå."
        if valid and not expires_soon:
            text = f"Gyldig ut {through}."
        elif signup is not None:
            prefix = f"Går ut etter {through}. " if expires_soon else ""
            text = f"{prefix}Påmeldt {session_label(signup.starts_at)}."
        elif expires_soon:
            text = f"Går ut etter {through}.{next_text}"
        elif completed is not None:
            text = f"Gikk ut etter {through}.{next_text}"
        else:
            text = f"Ikke tatt.{next_text}"
        return CourseStatus(
            key=key,
            name=rule.name,
            valid=valid,
            status_text=text,
            valid_through=through,
            expires_soon=expires_soon,
            signed_up=signed,
            sessions=sessions,
        )

    # ------------------------------------------------------------ evaluation

    def _evaluate(
        self,
        person: _Person,
        record: ShiftRecord,
        slot: SlotRecord,
        courses: _Courses,
        mine: _Mine,
        *,
        detail: bool = False,
    ) -> _Eval:
        """Can this person take this place? Mirrors ``check()`` in the prototype."""
        now = self._now()
        result = _Eval()
        problem = person.eligibility.problem(slot.kind, record.group_id, open_for_everyone(slot))
        on_shift = any(p.volunteer_id == person.volunteer_id for s in record.slots for p in s.people)
        free = open_places(record, slot)
        reasons = [
            (problem is not None, problem),
            (record.is_closed, "Skiftet er stengt"),
            (record.starts_at <= now, "Skiftet har startet"),
            (on_shift, "Du står allerede på dette skiftet"),
            (free <= 0, "Plassen ble nettopp tatt" if detail else "Fullt"),
        ]
        hidden_reason = next((text for hit, text in reasons if hit), None)
        if hidden_reason is not None:
            result.hidden = True
            result.block = hidden_reason
            if detail:
                result.requirements.append(Requirement(text=hidden_reason + ".", state="block"))
            return result

        if slot.kind == "skiftleder":
            result.requirements.append(Requirement(text=f"Skiftleder i {record.group_name}", state="ok"))
        elif slot.kind == "kokk":
            result.requirements.append(Requirement(text="Kokk på kjøkkenet", state="ok"))

        minimum = VENUE_MIN_AGE.get(record.venue)
        if minimum is not None:
            if person.birth_date is None:
                result.block = "Mangler fødselsdato i Personal. Kontakt lederen din"
                result.requirements.append(Requirement(text=result.block + ".", state="block"))
            elif age_on(person.birth_date, record.starts_at.astimezone(OSLO).date()) < minimum:
                result.block = f"{record.venue} krever {minimum} år"
                result.requirements.append(Requirement(text=result.block + ".", state="block"))
            else:
                result.requirements.append(Requirement(text=f"{minimum} år eller eldre", state="ok"))

        if slot.kind == "opplaering":
            done = self._training_done(mine, record.group_id)
            mode = (
                "Du tar en av plassene på skiftet, sammen med en utlært."
                if record.training_replaces_slot
                else "Du er med i tillegg til det vanlige skiftet."
            )
            result.training_text = f"Opplæringsskift {done + 1} av {TRAINING_SHIFTS_NEEDED}"
            result.requirements.append(Requirement(text=f"{result.training_text}. {mode}", state="ok"))
            trained_present = any(
                not entry.cannot_attend
                for item in record.slots
                if item.kind != "opplaering"
                for entry in item.people
            )
            if not trained_present:
                result.warnings.append("Ingen utlært på skiftet ennå")

        semester = semester_of(record.starts_at)
        # Groups that have not switched course checks on skip them entirely.
        checked = person.courses_enabled(record.group_id)
        for key in required_course_keys(slot.kind, person.is_skjenke_member) if checked else []:
            if key not in courses.ids:
                continue  # the course is not set up in Personal, so it cannot be checked
            rule = COURSE_RULES[key]
            completed = courses.completions.get(key)
            if course_valid_in(completed, semester):
                result.requirements.append(
                    Requirement(
                        text=f"{rule.name}, gyldig ut {semester_label(last_valid_semester(completed))}",  # type: ignore[arg-type]
                        state="ok",
                        course_key=key,
                    )
                )
                continue
            signup = courses.signups.get(key)
            if signup is not None and signup.starts_at < record.starts_at:
                text = f"Påmeldt {rule.name.lower()} {short_date(signup.starts_at)}"
                result.course_ok_text = result.course_ok_text or text
                result.requirements.append(Requirement(text=text, state="ok", course_key=key))
                continue
            result.missing.append(key)
            offer = next(
                (
                    row
                    for row in courses.sessions.get(key, [])
                    if now < row.starts_at < record.starts_at and row.spots_left > 0
                ),
                None,
            )
            why = (
                f"{rule.name} gikk ut etter {semester_label(last_valid_semester(completed))}."
                if completed is not None
                else f"{rule.name} mangler."
            )
            if offer is not None:
                result.offers.append(
                    CourseOffer(
                        course_key=key,
                        course_name=rule.name,
                        session_id=offer.session_id,
                        starts_at=offer.starts_at,
                        label=session_label(offer.starts_at),
                    )
                )
                result.requirements.append(
                    Requirement(
                        text=f"{why} Du må være påmeldt et kurs før skiftet. Neste: {session_label(offer.starts_at)}.",
                        state="warn",
                        course_key=key,
                    )
                )
            else:
                result.requirements.append(
                    Requirement(
                        text=f"{why} Det er ikke noe kurs før skiftet.",
                        state="block",
                        course_key=key,
                    )
                )

        result.warnings.extend(self._quota_warnings(person, record, slot, mine))
        for warning in result.warnings:
            result.requirements.append(Requirement(text=warning + ".", state="warn"))
        return result

    def _quota_warnings(self, person: _Person, record: ShiftRecord, slot: SlotRecord, mine: _Mine) -> list[str]:
        others = [
            (other, other_slot)
            for other, other_slot, _ in mine.entries()
            if other.shift_id != record.shift_id
        ]
        if person.is_skjenke_member:
            roles = person.roles_by_group.get(record.group_id) or set().union(*person.roles_by_group.values())
            rule = quota_for(roles)
            if rule.period == "month":
                local = record.starts_at.astimezone(OSLO)
                if any(_same_month(other.starts_at, local) for other, _ in others):
                    return [f"Har allerede et skift i {MONTHS_NO[local.month - 1]}"]
                return []
            week = iso_week_key(record.starts_at)
            if any(iso_week_key(other.starts_at) == week for other, _ in others):
                return [f"Har allerede et skift uke {week[1]}"]
            return []
        samf = [other for other, other_slot in others if counts_as_samf(False, other_slot.kind)]
        if counts_as_samf(False, slot.kind) and len(samf) >= SAMF_TARGET:
            return [f"Du har allerede {len(samf)} skift med samfunnettjeneste dette semesteret"]
        return []

    # ------------------------------------------------------------ building responses

    def _summary(self, record: ShiftRecord) -> ShiftSummary:
        day = record.starts_at.astimezone(OSLO).date()
        return ShiftSummary(
            shift_id=record.shift_id,
            group_id=record.group_id,
            group_name=record.group_name,
            venue=record.venue,
            venue_label=VENUE_LABELS.get(record.venue, record.venue),
            title=record.title,
            starts_at=record.starts_at,
            ends_at=record.ends_at,
            day=day,
            day_label=format_day(day),
            time_label=format_time_range(record.starts_at, record.ends_at),
            note=record.note,
            min_age=VENUE_MIN_AGE.get(record.venue),
        )

    def _open_place(self, person: _Person, record: ShiftRecord, slot: SlotRecord, evaluation: _Eval) -> OpenPlace:
        if evaluation.block:
            tone, status = "off", evaluation.block
        elif evaluation.missing:
            if evaluation.can_combo:
                first = evaluation.offers[0]
                verb = "Forny" if evaluation.missing[0] in _expired_keys(evaluation) else "Meld deg på"
                tone, status = "warn", f"{verb} {first.course_name.lower()} først · kurs {short_date(first.starts_at)}"
            else:
                key = next(k for k in evaluation.missing if k not in {o.course_key for o in evaluation.offers})
                tone, status = "off", f"Ingen {COURSE_RULES[key].name.lower()} før skiftet"
        elif evaluation.training_text:
            tone, status = "info", evaluation.training_text
        elif evaluation.warnings:
            tone, status = "warn", evaluation.warnings[0]
        elif evaluation.course_ok_text:
            tone, status = "ok", evaluation.course_ok_text
        else:
            tone, status = "ok", "Du kan ta denne"
        return OpenPlace(
            slot_id=slot.slot_id,
            kind=slot.kind,
            label=slot_kind(slot.kind).label,
            open_places=0 if evaluation.hidden else open_places(record, slot),
            opened_by_absence=opened_by_absence(slot),
            counts_as_samfunnettjeneste=counts_as_samf(person.is_skjenke_member, slot.kind),
            tone=tone,  # type: ignore[arg-type]
            status_text=status,
            can_claim=evaluation.can_claim,
            needs_course_signup=evaluation.offers if evaluation.can_combo else [],
        )

    async def _my_shift(self, person: _Person, slot_id: int) -> MyShift:
        shift_id = await self.repository.get_slot_shift_id(slot_id)
        record = await self.repository.get_shift(shift_id) if shift_id is not None else None
        if record is not None:
            for slot in record.slots:
                for entry in slot.people:
                    if slot.slot_id == slot_id and entry.volunteer_id == person.volunteer_id:
                        mine = await self._mine(person)
                        warnings = self._week_warnings(person, await self._published_only(mine.records))
                        return self._to_my_shift(
                            person, record, slot, entry, warnings.get(record.shift_id, [])
                        )
        raise ShiftNotFoundError("Finner ikke skiftet.")

    def _week_warnings(self, person: _Person, published: list[ShiftRecord]) -> dict[int, list[str]]:
        """Shift id -> hint, for each of the person's published shifts in a week with more than one."""
        if not person.member_enabled_ids:
            return {}  # samfunnettjeneste has no weekly contract
        roles = set().union(*(person.roles_by_group[gid] for gid in person.member_enabled_ids))
        rule = quota_for(roles)
        by_week: dict[tuple[int, int], list[ShiftRecord]] = {}
        for record in published:
            if any(p.volunteer_id == person.volunteer_id for s in record.slots for p in s.people):
                by_week.setdefault(iso_week_key(record.starts_at), []).append(record)
        result: dict[int, list[str]] = {}
        for (_, week_number), records in by_week.items():
            text = week_contract_warning(len(records), week_number, rule)
            if text:
                for record in records:
                    result[record.shift_id] = [text]
        return result

    def _to_my_shift(
        self,
        person: _Person,
        record: ShiftRecord,
        slot: SlotRecord,
        entry: AssignedPerson,
        warnings: list[str] | None = None,
    ) -> MyShift:
        now = self._now()
        samf = counts_as_samf(person.is_skjenke_member, slot.kind)
        if entry.cannot_attend:
            tag = "Du har sagt at du ikke kan. Plassen er åpen for andre."
        elif entry.swap_requested:
            tag = "Du ønsker bytte. Lederne får ikke varsel ennå."
        elif samf:
            tag = "Teller som samfunnettjeneste"
        elif slot.kind == "opplaering":
            tag = "Opplæringsskift"
        else:
            tag = None
        return MyShift(
            **self._summary(record).model_dump(),
            assignment_id=entry.assignment_id,
            slot_id=slot.slot_id,
            kind=slot.kind,
            label=slot_kind(slot.kind).label,
            tag=tag,
            counts_as_samfunnettjeneste=samf,
            swap_requested=entry.swap_requested,
            cannot_attend=entry.cannot_attend,
            can_request_swap=record.starts_at > now and not entry.cannot_attend,
            can_report_cannot_attend=not entry.cannot_attend
            and cannot_attend_window_open(record.starts_at, record.ends_at, now),
            meeting_point=person.meeting_point(record.group_id),
            description=SLOT_DESCRIPTIONS.get(slot.kind),
            crew=_crew(record),
            warnings=warnings or [],
        )

    async def _own_assignment(self, volunteer_id: int, assignment_id: int):
        person = await self._require_person(volunteer_id)
        state = await self.repository.get_assignment_state(assignment_id)
        if state is None or state.volunteer_id != person.volunteer_id:
            raise ShiftNotFoundError("Finner ikke skiftet. Kanskje noen har tatt over plassen.")
        record = await self.repository.get_shift(state.shift_id)
        if record is None or record.ends_at <= self._now():
            raise ShiftNotFoundError("Skiftet er over.")
        return person, state, record

    async def _set_status(self, assignment_id: int, *, swap: bool, cannot: bool, message: str | None) -> None:
        cleaned = (message or "").strip() or None
        await self.repository.set_assignment_status(
            assignment_id,
            swap_requested=swap,
            cannot_attend=cannot,
            message=cleaned,
            changed_at=self._now(),
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
