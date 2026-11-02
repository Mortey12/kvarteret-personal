"""The person's own shifts: list, swap and "kan ikke i dag"."""

from __future__ import annotations


from app.domain.shifts.app_models import (
    MyShift,
    MyShiftsResponse,
    QuotaSummary,
    SamfProgress,
    ShiftActionResponse,
)
from app.domain.shifts.app_rules import (
    SAMF_TARGET,
    SLOT_DESCRIPTIONS,
    cannot_attend_window_open,
    counts_as_samf,
    iso_week_key,
    quota_for,
    week_contract_warning,
)
from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import AssignedPerson, ShiftRecord, SlotRecord
from app.domain.shifts.planning import (
    MONTHS_NO,
    OSLO,
    slot_kind,
)
from app.domain.shifts.app_common import (
    _Person,
    _crew,
    _same_month,
)


class MineMixin:
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
