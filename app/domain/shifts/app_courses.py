"""Course status, sign-up and leaving a session."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from app.domain.shifts.app_models import (
    CourseSessionOut,
    CourseStatus,
    ShiftCoursesResponse,
)
from app.domain.shifts.app_repository import (
    SessionRow,
    SignupRow,
)
from app.domain.shifts.app_rules import (
    BAR_COURSE,
    COURSE_RULES,
    FIRE_COURSE,
    course_valid_in,
    last_valid_semester,
    required_course_keys,
    semester_label,
    semester_of,
    session_label,
)
from app.domain.shifts.errors import ShiftNotFoundError, ShiftPlacementError
from app.domain.shifts.models import ShiftRecord
from app.domain.shifts.planning import (
    OSLO,
    format_day,
)
from app.domain.shifts.app_common import (
    COURSE_HORIZON_DAYS,
    _Courses,
    _Person,
)


class CoursesMixin:
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
