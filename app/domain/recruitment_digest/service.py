"""Digest emails for each group's rekrutteringsansvarlig.

One email per recipient and group per day, so a busy day of applications never
becomes a burst of messages. The digest covers new applicants, applicants still waiting
(``new``) after 3 and 7 days, and trials with 7 days left or just finished.
Recipients are the group's recruitment contact address and/or chosen person (both when both
are set), falling back to the group's current leaders.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape

from sqlalchemy import func, or_, select

from app.db.session import current_session
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.volunteer_applications.tables import (
    domain_events,
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

# Leaders have many titles ("Leder", "Finansleder", "Revysjef", "President",
# "Leder Padde"), so match on the title rather than one exact name. Deputies
# and assistants never count.
LEADER_ROLE_LIKE = (
    "%leder%",
    "%sjef%",
    "president",
    "rittmester",
    "sirenedronning",
)
# Roles that own applications; the previous semester still counts because
# holders are often not re-registered right away.
RECRUITER_ROLE_LIKE = ("%rekrutter%", "%søknad%")
NOT_LEADER_ROLE_LIKE = ("%nestleder%", "vise%", "assisterende%")
BUSINESS_TYPE = "recruitment_digest"

# Section key -> heading, in display order.
SECTION_TITLES = {
    "new": "Nye søkere",
    "waiting_3d": "Søkere som har ventet i 3 dager",
    "waiting_7d": "Søkere som har ventet i 7 dager",
    "trial_7d": "Prøvetid: 7 dager igjen",
    "trial_done": "Prøvetid: ferdig (0 dager igjen)",
    # Snapshot digest: where things stand right now.
    "waiting": "Søkere som venter på svar",
    "contacted": "Kontaktet",
    "trial_active": "På prøvetid nå",
}

# Section -> accent colour: red needs action, green is new.
SECTION_COLORS = {
    "new": "#2f8f4e",
    "waiting": "#db242a",
    "waiting_3d": "#db242a",
    "waiting_7d": "#db242a",
    "contacted": "#b26a00",
    "trial_done": "#db242a",
    "trial_7d": "#6f655e",
    "trial_active": "#6f655e",
}
TRIAL_SECTIONS = frozenset({"trial_7d", "trial_done", "trial_active"})

# Long sections show the first five and "Og X til", but only when more than
# ten would be hidden; otherwise everyone is listed.
# Safety fuse: a normal day queues a handful of digests. If a bug or bad data
# ever produces far more, queue nothing and log an error instead of spamming.
MAX_DIGESTS_PER_RUN = 60
MAX_LISTED_PER_SECTION = 5
MAX_HIDDEN_BEFORE_COLLAPSE = 10
DRY_RUN_KEY = "_dry_run_for"
MISSING_KEY = "_missing_groups"
GROUP_KEY = "_group_id"
NAME_KEY = "_name"
# Events that put an application into the "contacted" status.
CONTACT_EVENT_TYPES = ("application_contacted", "application_reopened")

_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class DigestCandidate:
    registration_id: int
    status: str
    created_at: datetime
    trial_ends_at: datetime | None
    # Groups the applicant applied to; trial sections use ``trial_group_id``.
    group_ids: tuple[int, ...]
    trial_group_id: int | None = None
    contacted_at: datetime | None = None


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


def classify_snapshot(candidate: DigestCandidate, now: datetime) -> list[str]:
    """Sections for the current-status digest, independent of day windows."""
    if candidate.status == "new":
        return ["waiting"]
    if candidate.status == "contacted":
        return ["contacted"]
    if candidate.status == "trial" and candidate.trial_ends_at is not None:
        ends = _aware(candidate.trial_ends_at)
        return ["trial_active"] if ends > now else ["trial_done"]
    return []


def _previous_semester(code: int) -> int:
    year, term = divmod(code, 10)
    return year * 10 + 1 if term == 2 else (year - 1) * 10 + 2


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class RecruitmentDigestService:
    def __init__(self, *, email_outbox: EmailDeliveryOutboxProtocol) -> None:
        self.email_outbox = email_outbox

    async def enqueue_daily_digests(self, *, now: datetime | None = None) -> int:
        """Queue one digest per recipient and group. Caller commits and dispatches."""
        now = now or datetime.now(UTC)
        digests, _missing = await self._collect(now, snapshot=False)
        if len(digests) > MAX_DIGESTS_PER_RUN:
            logger.error(
                "recruitment_digest.fuse_tripped",
                extra={
                    "event": "recruitment_digest.fuse_tripped",
                    "digest_count": len(digests),
                },
            )
            return 0
        today = now.date().isoformat()
        for (recipient, group_id), item in digests.items():
            await self._enqueue(
                recipient,
                item,
                group_id,
                key=f"recruiter-digest:{today}:{group_id}:{recipient}",
                business_id=today,
            )
        return len(digests)

    async def enqueue_status_digests(
        self, *, now: datetime | None = None, redirect_to: str | None = None
    ) -> tuple[list[dict], list[dict]]:
        """Queue a current-status digest per recipient and group (a one-off).

        With ``redirect_to`` every digest goes to that address instead, marked
        as a dry run for the real recipient, and one extra email lists groups
        that have applicants but nobody to send to. Real sends never include
        that list. Returns what was queued and the groups without a recipient.
        """
        now = now or datetime.now(UTC)
        digests, missing = await self._collect(now, snapshot=True)
        missing_groups = await self._group_names(missing)
        stamp = now.strftime("%Y-%m-%dT%H%M")
        queued = []
        for (recipient, group_id), item in digests.items():
            queued.append(
                {
                    "recipient": recipient,
                    "group_id": group_id,
                    "sections": {k: len(v) for k, v in item["sections"].items()},
                }
            )
            if redirect_to:
                await self._enqueue(
                    redirect_to,
                    item,
                    group_id,
                    key=f"recruiter-status-dryrun:{stamp}:{group_id}:{recipient}",
                    business_id=stamp,
                    extra={DRY_RUN_KEY: recipient},
                )
            else:
                await self._enqueue(
                    recipient,
                    item,
                    group_id,
                    key=f"recruiter-status:{stamp}:{group_id}:{recipient}",
                    business_id=stamp,
                )
        if redirect_to and missing_groups:
            await self.email_outbox.enqueue(
                EmailDeliveryRequest(
                    template_key=RECRUITER_DAILY_DIGEST,
                    template_version=1,
                    recipient_email=redirect_to,
                    business_type=BUSINESS_TYPE,
                    business_id=stamp,
                    idempotency_key=f"recruiter-status-dryrun-missing:{stamp}",
                    encrypted_context=json.dumps(
                        {MISSING_KEY: missing_groups}
                    ).encode(),
                )
            )
        return queued, missing_groups

    async def _group_names(self, counts: dict[int, int]) -> list[dict]:
        if not counts:
            return []
        rows = (
            await _session().execute(
                select(groups.c.id, groups.c.name).where(groups.c.id.in_(list(counts)))
            )
        ).all()
        return sorted(
            (
                {"group_id": g, "name": n, "applicants": counts[g]}
                for g, n in rows
            ),
            key=lambda item: item["name"].lower(),
        )

    async def _enqueue(
        self,
        recipient: str,
        item: dict,
        group_id: int,
        *,
        key: str,
        business_id: str,
        extra: dict | None = None,
    ) -> None:
        context = {
            **item["sections"],
            GROUP_KEY: group_id,
            NAME_KEY: item["name"],
            **(extra or {}),
        }
        await self.email_outbox.enqueue(
            EmailDeliveryRequest(
                template_key=RECRUITER_DAILY_DIGEST,
                template_version=1,
                recipient_email=recipient,
                business_type=BUSINESS_TYPE,
                business_id=business_id,
                idempotency_key=key,
                # Only application ids and the recruiter's first name; applicant
                # names are loaded when the email is prepared so the outbox
                # holds no applicant PII.
                encrypted_context=json.dumps(context).encode(),
            )
        )

    async def _collect(
        self, now: datetime, *, snapshot: bool
    ) -> tuple[dict[tuple[str, int], dict], dict[int, int]]:
        """Return (recipient, group) -> {name, sections: section -> ids} and the
        applicant count per group that has no recipient at all."""
        candidates = await self._load_candidates(now, snapshot=snapshot)
        digests: dict[tuple[str, int], dict] = {}
        recipients_by_group: dict[int, list[tuple[str, str | None]]] = {}
        missing: dict[int, int] = {}
        for candidate in candidates:
            sections = (
                classify_snapshot(candidate, now) if snapshot else classify(candidate, now)
            )
            seen_missing: set[int] = set()
            for section in sections:
                if section in TRIAL_SECTIONS:
                    group_ids = (candidate.trial_group_id,) if candidate.trial_group_id else ()
                else:
                    group_ids = candidate.group_ids
                for group_id in group_ids:
                    if group_id not in recipients_by_group:
                        recipients_by_group[group_id] = await self._recipients(group_id)
                    if not recipients_by_group[group_id] and group_id not in seen_missing:
                        seen_missing.add(group_id)
                        missing[group_id] = missing.get(group_id, 0) + 1
                    for email, name in recipients_by_group[group_id]:
                        item = digests.setdefault(
                            (email, group_id), {"name": name, "sections": {}}
                        )
                        ids = item["sections"].setdefault(section, [])
                        if candidate.registration_id not in ids:
                            ids.append(candidate.registration_id)
        return digests, missing

    async def _load_candidates(
        self, now: datetime, *, snapshot: bool = False
    ) -> list[DigestCandidate]:
        invites = volunteer_application_invites
        trial_group = (
            select(role_assignments.c.group_id)
            .where(role_assignments.c.id == invites.c.trial_assignment_id)
            .scalar_subquery()
        )
        contacted_at = (
            select(func.max(domain_events.c.occurred_at))
            .where(
                domain_events.c.subject_type == "application",
                domain_events.c.subject_id == invites.c.id,
                domain_events.c.event_type.in_(CONTACT_EVENT_TYPES),
            )
            .scalar_subquery()
        )
        rows = (
            (
                await _session().execute(
                    select(
                        contacted_at.label("contacted_at"),
                        invites.c.id,
                        invites.c.status,
                        invites.c.created_at,
                        invites.c.trial_ends_at,
                        invites.c.first_choice_group_id,
                        invites.c.second_choice_group_id,
                        invites.c.initial_group_id,
                        trial_group.label("trial_group_id"),
                    ).where(
                        invites.c.status.in_(("new", "contacted", "trial"))
                        if snapshot
                        else (invites.c.created_at > now - 8 * _DAY)
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
        return [
            DigestCandidate(
                registration_id=row["id"],
                status=row["status"],
                created_at=row["created_at"],
                trial_ends_at=row["trial_ends_at"],
                group_ids=tuple(
                    dict.fromkeys(
                        g
                        for g in (row["first_choice_group_id"], row["second_choice_group_id"])
                        if g is not None
                    )
                ),
                contacted_at=row["contacted_at"],
                trial_group_id=(
                    (
                        row["trial_group_id"]
                        or row["initial_group_id"]
                        or row["first_choice_group_id"]
                    )
                    if row["status"] == "trial"
                    else None
                ),
            )
            for row in rows
        ]

    async def _recipients(self, group_id: int) -> list[tuple[str, str | None]]:
        """(email, first name or None) for everyone who gets this group's digest."""
        session = _session()
        # The explicit address and the chosen person both receive the digest;
        # leaders are only the fallback when neither is set.
        contact = (
            await session.execute(
                select(
                    groups.c.recruitment_contact_email,
                    volunteer_records.c.email.label("person_email"),
                    volunteer_records.c.first_name.label("person_first_name"),
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
        ).mappings().first() or {}
        people: dict[str, str | None] = {}
        explicit = (contact.get("recruitment_contact_email") or "").strip().lower()
        if explicit:
            people[explicit] = None
        person_email = (contact.get("person_email") or "").strip().lower()
        if person_email:
            people[person_email] = contact.get("person_first_name") or people.get(person_email)
        if people:
            return sorted(people.items())
        # Ideal recipients first: whoever owns applications/recruiting, then
        # the group's leaders.
        current = get_current_semester_code()
        for patterns, not_patterns, min_semester in (
            (RECRUITER_ROLE_LIKE, (), _previous_semester(current)),
            (LEADER_ROLE_LIKE, NOT_LEADER_ROLE_LIKE, current),
        ):
            holders = await self._role_holders(
                group_id, patterns, not_patterns, min_semester
            )
            if holders:
                return holders
        logger.warning(
            "recruitment_digest.no_recipient",
            extra={"event": "recruitment_digest.no_recipient", "group_id": group_id},
        )
        return []

    async def _role_holders(
        self,
        group_id: int,
        patterns: tuple[str, ...],
        not_patterns: tuple[str, ...],
        min_semester: int,
    ) -> list[tuple[str, str | None]]:
        name = func.lower(func.trim(assignment_roles.c.name))
        rows = (
            await _session().execute(
                select(volunteer_records.c.email, volunteer_records.c.first_name)
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
                    role_assignments.c.semester >= min_semester,
                    or_(*(name.like(pattern) for pattern in patterns)),
                    *(~name.like(pattern) for pattern in not_patterns),
                    volunteer_records.c.email.is_not(None),
                )
            )
        ).all()
        return sorted(
            {
                email.strip().lower(): first_name
                for email, first_name in rows
                if email and email.strip()
            }.items()
        )


_INK = "#26211e"
_MUTED = "#6f655e"
_BORDER = "#e4ddd3"
_FONT = "DM Sans, Arial, Helvetica, sans-serif"


def parse_digest_context(raw: bytes) -> dict | None:
    """Decode a queued digest context, or None if its shape is not understood.

    The release that queues a digest can differ from the one that renders it,
    so an unknown shape must fail with a clear category, not a TypeError.
    """
    try:
        context = json.loads(bytes(raw))
    except ValueError:
        return None
    if not isinstance(context, dict):
        return None

    def is_id(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    missing = context.get(MISSING_KEY) or []
    valid = (
        context.keys() <= SECTION_TITLES.keys() | {GROUP_KEY, NAME_KEY, DRY_RUN_KEY, MISSING_KEY}
        and all(
            isinstance(ids := context.get(key, []), list) and all(map(is_id, ids))
            for key in SECTION_TITLES
        )
        and (context.get(GROUP_KEY) is None or is_id(context[GROUP_KEY]))
        and isinstance(context.get(NAME_KEY) or "", str)
        and isinstance(context.get(DRY_RUN_KEY) or "", str)
        and isinstance(missing, list)
        and all(
            isinstance(item, dict)
            and is_id(item.get("group_id"))
            and isinstance(item.get("name"), str)
            and is_id(item.get("applicants"))
            for item in missing
        )
    )
    return context if valid else None


def _days_ago(moment: datetime, now: datetime) -> int:
    return max(0, (now - _aware(moment)).days)


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _age_text(days: int) -> str:
    return "i dag" if days == 0 else "for 1 dag siden" if days == 1 else f"for {days} dager siden"


async def render_digest(
    context: dict, *, base_url: str, now: datetime | None = None
) -> tuple[str, str, str | None, int]:
    """Build one group's digest.

    Returns (sections_html, group_name, recruiter first name, applicant count).
    Applicants link to their application and the footer link opens the
    application list filtered to the group.
    """
    now = now or datetime.now(UTC)
    base = escape(base_url, quote=True)
    group_id = context.get(GROUP_KEY)
    sections = {
        key: [int(reg) for reg in context.get(key, [])] for key in SECTION_TITLES
    }
    ids = sorted({reg for items in sections.values() for reg in items})
    people: dict[int, dict] = {}
    group_name = ""
    if group_id is not None:
        group_name = (
            await _session().scalar(select(groups.c.name).where(groups.c.id == int(group_id)))
        ) or ""
    if ids:
        for row in (
            (
                await _session().execute(
                    select(
                        volunteer_application_invites.c.id,
                        volunteer_application_invites.c.email,
                        volunteer_application_invites.c.created_at,
                        volunteer_application_invites.c.trial_ends_at,
                        select(func.max(domain_events.c.occurred_at))
                        .where(
                            domain_events.c.subject_type == "application",
                            domain_events.c.subject_id == volunteer_application_invites.c.id,
                            domain_events.c.event_type.in_(CONTACT_EVENT_TYPES),
                        )
                        .scalar_subquery()
                        .label("contacted_at"),
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
        ):
            full = " ".join(filter(None, [row["first_name"], row["last_name"]])).strip()
            people[row["id"]] = {
                "name": full or row["email"],
                "created_at": row["created_at"],
                "trial_ends_at": row["trial_ends_at"],
                "contacted_at": row["contacted_at"],
            }

    def row_meta(key: str, person: dict) -> tuple[str, int | None]:
        if key in TRIAL_SECTIONS and person["trial_ends_at"] is not None:
            ends = _aware(person["trial_ends_at"])
            if ends > now:
                days = max(0, (ends - now).days)
                return f"{_plural(days, 'dag', 'dager')} igjen av prøvetiden", days
            days = _days_ago(ends, now)
            return f"Prøvetiden utløp {_age_text(days)}", days
        if key == "contacted":
            # Fall back to the application date if no contact event was logged.
            days = _days_ago(person["contacted_at"] or person["created_at"], now)
            return f"Kontaktet {_age_text(days)}", days
        days = _days_ago(person["created_at"], now)
        return f"Søkte {_age_text(days)}", days

    def sort_key(key: str, reg: int) -> float:
        person = people[reg]
        if key in ("trial_active", "trial_7d") and person["trial_ends_at"]:
            return _aware(person["trial_ends_at"]).timestamp()
        if key == "contacted":
            return _aware(person["contacted_at"] or person["created_at"]).timestamp()
        return _aware(person["created_at"]).timestamp()

    cards: list[str] = []
    listed: set[int] = set()
    for key, title in SECTION_TITLES.items():
        items = sorted([r for r in sections[key] if r in people], key=lambda r: sort_key(key, r))
        if not items:
            continue
        listed.update(items)
        color = SECTION_COLORS[key]
        rows = []
        collapse = len(items) > MAX_LISTED_PER_SECTION + MAX_HIDDEN_BEFORE_COLLAPSE
        for reg in items[:MAX_LISTED_PER_SECTION] if collapse else items:
            person = people[reg]
            meta, days = row_meta(key, person)
            rows.append(
                f'<tr><td style="padding:12px 16px;border-top:1px solid {_BORDER};font-family:{_FONT}">'
                f'<a href="{base}/volunteer-applications/{reg}" style="color:{_INK};font-weight:700;'
                f'font-size:15px;text-decoration:none">{escape(person["name"])}</a>'
                f'<div style="font-size:13px;color:{_MUTED};margin-top:2px">{escape(meta)}</div></td>'
                f'<td style="padding:12px 16px;border-top:1px solid {_BORDER};text-align:right;'
                f'font-family:{_FONT};font-size:18px;font-weight:700;color:{color};white-space:nowrap">'
                f'{"" if days is None else days}</td></tr>'
            )
        if collapse:
            rows.append(
                f'<tr><td colspan="2" style="padding:12px 16px;border-top:1px solid {_BORDER};'
                f'font-family:{_FONT};font-size:14px;color:{_MUTED}">'
                f"Og {len(items) - MAX_LISTED_PER_SECTION} til</td></tr>"
            )
        cards.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-left:4px solid {color};background:#faf8f4;margin:0 0 16px">'
            f'<tr><td colspan="2" style="padding:16px;font-family:{_FONT};font-size:16px;'
            f'font-weight:700;color:{color}">{escape(title)}</td></tr>{"".join(rows)}</table>'
        )

    parts = list(cards)
    if cards and group_id is not None:
        list_url = (
            f"{base}/volunteer-applications?groups_selected=true&amp;q=&amp;group_ids={int(group_id)}"
        )
        link = (
            f'<a href="{list_url}" style="color:#db242a;font-weight:700;font-size:15px;'
            f'text-decoration:none">%s →</a>'
        )
        parts.insert(0, f'<p style="margin:0 0 16px;font-family:{_FONT}">{link % "Til søknadsside"}</p>')
        parts.append(f'<p style="margin:8px 0 24px;font-family:{_FONT}">{link % "Til søknadene"}</p>')
    missing = context.get(MISSING_KEY) or []
    if missing:
        rows_html = "".join(
            f'<li style="margin:0 0 4px"><a href="{base}/groups/{int(m["group_id"])}" '
            f'style="color:{_INK}">{escape(m["name"])}</a> '
            f'({_plural(int(m["applicants"]), "søker", "søkere")})</li>'
            for m in missing
        )
        parts.append(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-left:4px solid #e8b35a;background:#fff7e6;margin:0 0 16px">'
            f'<tr><td style="padding:16px;font-family:{_FONT};font-size:14px;color:{_INK}">'
            f'<div style="font-weight:700;font-size:16px;margin-bottom:6px">Grupper uten mottaker</div>'
            f'<ul style="padding-left:20px;margin:8px 0 0">{rows_html}</ul></td></tr></table>'
        )
    return "".join(parts), group_name, context.get(NAME_KEY), len(listed)


def _session():
    session = current_session()
    if session is None:
        raise RuntimeError("Recruitment digest requires an active database session.")
    return session
