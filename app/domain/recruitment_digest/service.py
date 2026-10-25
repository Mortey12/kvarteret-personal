"""Daily digest emails for each group's rekrutteringsansvarlig.

One email per recipient per day, so a busy day of applications never becomes a
burst of messages. The digest covers new applicants, applicants still waiting
(``new``) after 3 and 7 days, and trials with 7 days left or just finished.
Recipients are the group's recruitment contact address and/or chosen person (both when both
are set), falling back to the group's current leaders.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape

from sqlalchemy import func, select

from app.db.session import current_session
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.volunteer_applications.tables import (
    volunteer_application_invites,
    volunteer_application_submissions,
)
from app.domain.volunteers.tables import volunteer_records
from app.email_delivery import (
    RECRUITER_DAILY_DIGEST,
    EmailDeliveryOutboxProtocol,
    EmailDeliveryRequest,
)
from app.observability import get_logger
from app.shared.semester import get_current_semester_code

logger = get_logger(__name__)

LEADER_ROLE_NAMES = ("leder", "gruppeleder")
BUSINESS_TYPE = "recruitment_digest"

# Section key -> heading, in display order.
SECTION_TITLES = {
    "new": "Nye søkere",
    "waiting_3d": "Søkere som har ventet i 3 dager",
    "waiting_7d": "Søkere som har ventet i 7 dager",
    "trial_7d": "Prøvetid: 7 dager igjen",
    "trial_done": "Prøvetid: ferdig (0 dager igjen)",
}

_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class DigestCandidate:
    registration_id: int
    status: str
    created_at: datetime
    trial_ends_at: datetime | None
    group_ids: tuple[int, ...]


def classify(candidate: DigestCandidate, now: datetime) -> list[str]:
    """Return the digest sections this application belongs to at ``now``.

    Each section is a half-open day window ``(start, end]``, so a daily run
    reports every application exactly once.
    """

    def within(value: datetime | None, start: timedelta, end: timedelta) -> bool:
        return value is not None and now - start < value <= now - end

    created = _aware(candidate.created_at)
    ends = _aware(candidate.trial_ends_at) if candidate.trial_ends_at else None
    sections: list[str] = []
    if within(created, _DAY, timedelta(0)):
        sections.append("new")
    if candidate.status == "new":
        if within(created, 4 * _DAY, 3 * _DAY):
            sections.append("waiting_3d")
        if within(created, 8 * _DAY, 7 * _DAY):
            sections.append("waiting_7d")
    if candidate.status == "trial" and ends is not None:
        # trial_ends_at lies in the future for 7d, so mirror the window.
        if now + 6 * _DAY < ends <= now + 7 * _DAY:
            sections.append("trial_7d")
        if within(ends, _DAY, timedelta(0)):
            sections.append("trial_done")
    return sections


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class RecruitmentDigestService:
    def __init__(self, *, email_outbox: EmailDeliveryOutboxProtocol) -> None:
        self.email_outbox = email_outbox

    async def enqueue_daily_digests(self, *, now: datetime | None = None) -> int:
        """Queue one digest per recipient. Caller commits and dispatches."""
        now = now or datetime.now(UTC)
        candidates = await self._load_candidates(now)
        # recipient -> section -> registration ids
        digests: dict[str, dict[str, list[int]]] = {}
        recipients_by_group: dict[int, list[str]] = {}
        for candidate in candidates:
            sections = classify(candidate, now)
            if not sections:
                continue
            for group_id in candidate.group_ids:
                if group_id not in recipients_by_group:
                    recipients_by_group[group_id] = await self._recipients(group_id)
                for recipient in recipients_by_group[group_id]:
                    per_section = digests.setdefault(recipient, {})
                    for section in sections:
                        ids = per_section.setdefault(section, [])
                        if candidate.registration_id not in ids:
                            ids.append(candidate.registration_id)
        today = now.date().isoformat()
        for recipient, per_section in digests.items():
            await self.email_outbox.enqueue(
                EmailDeliveryRequest(
                    template_key=RECRUITER_DAILY_DIGEST,
                    template_version=1,
                    recipient_email=recipient,
                    business_type=BUSINESS_TYPE,
                    business_id=today,
                    idempotency_key=f"recruiter-digest:{today}:{recipient}",
                    # Only application ids; names are loaded when the email is
                    # prepared so the outbox holds no applicant PII.
                    encrypted_context=json.dumps(per_section).encode(),
                )
            )
        return len(digests)

    async def _load_candidates(self, now: datetime) -> list[DigestCandidate]:
        invites = volunteer_application_invites
        trial_group = (
            select(role_assignments.c.group_id)
            .where(role_assignments.c.id == invites.c.trial_assignment_id)
            .scalar_subquery()
        )
        rows = (
            (
                await _session().execute(
                    select(
                        invites.c.id,
                        invites.c.status,
                        invites.c.created_at,
                        invites.c.trial_ends_at,
                        invites.c.first_choice_group_id,
                        invites.c.second_choice_group_id,
                        invites.c.initial_group_id,
                        trial_group.label("trial_group_id"),
                    ).where(
                        (invites.c.created_at > now - 8 * _DAY)
                        | (
                            (invites.c.status == "trial")
                            & (invites.c.trial_ends_at > now - _DAY)
                            & (invites.c.trial_ends_at <= now + 7 * _DAY)
                        )
                    )
                )
            )
            .mappings()
            .all()
        )
        candidates = []
        for row in rows:
            if row["status"] == "trial":
                group_ids = [
                    row["trial_group_id"]
                    or row["initial_group_id"]
                    or row["first_choice_group_id"]
                ]
                # A trial's own group is the interesting one for trial sections,
                # but the applicant also counts as an applicant to both choices.
                group_ids += [
                    row["first_choice_group_id"],
                    row["second_choice_group_id"],
                ]
            else:
                group_ids = [
                    row["first_choice_group_id"],
                    row["second_choice_group_id"],
                ]
            unique = tuple(dict.fromkeys(g for g in group_ids if g is not None))
            if unique:
                candidates.append(
                    DigestCandidate(
                        registration_id=row["id"],
                        status=row["status"],
                        created_at=row["created_at"],
                        trial_ends_at=row["trial_ends_at"],
                        group_ids=unique,
                    )
                )
        return candidates

    async def _recipients(self, group_id: int) -> list[str]:
        session = _session()
        # The explicit address and the chosen person both receive the digest;
        # leaders are only the fallback when neither is set.
        contact = (
            await session.execute(
                select(
                    groups.c.recruitment_contact_email,
                    volunteer_records.c.email.label("person_email"),
                )
                .select_from(
                    groups.outerjoin(
                        volunteer_records,
                        volunteer_records.c.id
                        == groups.c.recruitment_contact_volunteer_id,
                    )
                )
                .where(groups.c.id == group_id)
            )
        ).mappings().first()
        explicit = sorted(
            {
                value.strip().lower()
                for value in (
                    (contact or {}).get("recruitment_contact_email"),
                    (contact or {}).get("person_email"),
                )
                if value and value.strip()
            }
        )
        if explicit:
            return explicit
        emails = (
            await session.scalars(
                select(volunteer_records.c.email)
                .select_from(
                    role_assignments.join(
                        assignment_roles,
                        assignment_roles.c.id == role_assignments.c.role_id,
                    ).join(
                        volunteer_records,
                        volunteer_records.c.id == role_assignments.c.volunteer_id,
                    )
                )
                .where(
                    role_assignments.c.group_id == group_id,
                    role_assignments.c.semester == get_current_semester_code(),
                    func.lower(func.trim(assignment_roles.c.name)).in_(
                        LEADER_ROLE_NAMES
                    ),
                    volunteer_records.c.email.is_not(None),
                )
            )
        ).all()
        recipients = sorted({e.strip().lower() for e in emails if e and e.strip()})
        if not recipients:
            logger.warning(
                "recruitment_digest.no_recipient",
                extra={"event": "recruitment_digest.no_recipient", "group_id": group_id},
            )
        return recipients


async def render_digest_sections(
    per_section: dict[str, list[int]], *, base_url: str
) -> tuple[str, int]:
    """Build escaped section markup and the number of listed applications."""
    ids = sorted({i for values in per_section.values() for i in values})
    names: dict[int, str] = {}
    if ids:
        rows = (
            (
                await _session().execute(
                    select(
                        volunteer_application_invites.c.id,
                        volunteer_application_invites.c.email,
                        volunteer_application_submissions.c.first_name,
                        volunteer_application_submissions.c.last_name,
                    )
                    .select_from(
                        volunteer_application_invites.outerjoin(
                            volunteer_application_submissions,
                            volunteer_application_submissions.c.invite_id
                            == volunteer_application_invites.c.id,
                        )
                    )
                    .where(volunteer_application_invites.c.id.in_(ids))
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            full = " ".join(filter(None, [row["first_name"], row["last_name"]])).strip()
            names[row["id"]] = full or row["email"]
    parts: list[str] = []
    listed: set[int] = set()
    for key, title in SECTION_TITLES.items():
        items = [i for i in per_section.get(key, []) if i in names]
        if not items:
            continue
        listed.update(items)
        links = "".join(
            f'<li style="margin:0 0 6px"><a href="{escape(base_url, quote=True)}'
            f'/volunteer-applications/{i}">{escape(names[i])}</a></li>'
            for i in items
        )
        parts.append(
            f'<h2 style="font-size:20px;margin:24px 0 8px">{escape(title)}</h2>'
            f'<ul style="padding-left:20px;margin:0">{links}</ul>'
        )
    return "".join(parts), len(listed)


def _session():
    session = current_session()
    if session is None:
        raise RuntimeError("Recruitment digest requires an active database session.")
    return session
