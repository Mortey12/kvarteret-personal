"""Config, open places, taking a place and the evaluation behind them."""

from __future__ import annotations

from datetime import timedelta

from app.domain.shifts.app_models import (
    Audience,
    ClaimPlaceRequest,
    CourseOffer,
    LabeledValue,
    OpenPlace,
    OpenScope,
    OpenShift,
    OpenShiftsResponse,
    PlaceDetailResponse,
    Requirement,
    ShiftActionResponse,
    ShiftAppConfigResponse,
    ShiftAppGroup,
    ShiftAppTab,
    ShiftSlotKind,
    ShiftSummary,
)
from app.domain.shifts.app_rules import (
    COURSE_RULES,
    SAMF_TARGET,
    SLOT_DESCRIPTIONS,
    TRAINING_SHIFTS_NEEDED,
    WISH_ANSWERS,
    counts_as_samf,
    course_valid_in,
    iso_week_key,
    last_valid_semester,
    oslo_midnight,
    quota_for,
    required_course_keys,
    semester_label,
    semester_of,
    session_label,
    short_date,
)
from app.domain.shifts.errors import ShiftPlacementError
from app.domain.shifts.models import ShiftRecord, SlotRecord
from app.domain.shifts.planning import (
    MONTHS_NO,
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
    open_for_everyone,
    open_places,
    opened_by_absence,
    slot_kind,
)
from app.domain.shifts.app_common import (
    MAX_OPEN_DAYS,
    _Courses,
    _Eval,
    _Mine,
    _Person,
    _crew,
    _expired_keys,
    _role_label,
    _same_month,
    _sentence,
    logger,
)


class ClaimMixin:
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
            result.requirements.append(Requirement(text="Godkjent skiftleder", state="ok"))
        elif slot.kind == "kokk":
            result.requirements.append(Requirement(text="Godkjent kokk", state="ok"))

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
