"""Read-only readiness report for a Skiftlista pilot (one command, changes nothing).

Run it with database access. It reads ``DATABASE_URL`` and prints, per bar, what
is missing before a pilot and how many people and rows are involved. Output is
counts only: no names, no emails, no volunteer ids.

    uv run python scripts/shifts/pilot_readiness.py
    uv run python scripts/shifts/pilot_readiness.py --bars Stjerne,Kjøkken
    uv run python scripts/shifts/pilot_readiness.py --strict   # exit 1 when something is MISSING

Read-only on purpose: the transaction is opened READ ONLY on Postgres and always
rolled back, and the script contains only SELECT statements. Status words:
``OK`` (ready), ``MISSING`` (must be done before a pilot), ``CHECK`` (a number
or a fact to look at; it may be fine).

What it checks, per bar:

- the group exists under Skjenkegruppen and is active;
- the participating-group switch (``app_enabled``) is on;
- active shift templates, and shifts generated from now to the end of the
  semester;
- closed periods (holidays) that have not ended;
- how many people hold Skiftleder, Kokk and Opplæring verv this semester;
- how many qualification rows the verv copy script would add (the same rules as
  ``copy_verv_to_qualifications.py``, which this script imports);
- people with a verv here and no email;
- skiftledere who have no login or no Gruppeadmin assignment covering the bar.

Plus one global fact: whether fake (``@example.dev``) people are present, which
means this is not a real database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app.auth.roles import UserRole
from app.config import Settings
from app.db.session import build_database_runtime, session_scope
from app.domain.admin_accounts.tables import group_admin_memberships, user_accounts
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.tables import (
    shift_closed_periods,
    shift_group_settings,
    shift_templates,
    shifts,
    volunteer_qualifications,
)
from app.domain.volunteers.tables import volunteer_records
from app.shared.semester import get_current_semester_code, get_semester_end_date
from scripts.shifts import copy_verv_to_qualifications as copy

PARENT_GROUP_NAME = "Skjenkegruppen"
DEFAULT_BARS = ("Stjerne", "Kjøkken", "Grøndahls", "Halvtimen")
FAKE_EMAIL_DOMAIN = "@example.dev"
OSLO = ZoneInfo("Europe/Oslo")

OK = "OK"
MISSING = "MISSING"
CHECK = "CHECK"


@dataclass(frozen=True, slots=True)
class Check:
    status: str
    label: str
    detail: str = ""


@dataclass(slots=True)
class BarReport:
    name: str
    checks: list[Check] = field(default_factory=list)


@dataclass(slots=True)
class ReadinessReport:
    host: str | None
    semester: int
    global_checks: list[Check] = field(default_factory=list)
    bars: list[BarReport] = field(default_factory=list)

    def missing_count(self) -> int:
        every = [*self.global_checks, *(c for bar in self.bars for c in bar.checks)]
        return sum(1 for c in every if c.status == MISSING)


def _count_check(label: str, count: int, *, need: int = 1, hint: str = "") -> Check:
    detail = str(count) + (f" ({hint})" if hint and count < need else "")
    return Check(OK if count >= need else MISSING, label, detail)


def gruppeadmin_covers(
    group_id: int,
    admin_groups: set[int],
    parent_of: Mapping[int, int | None],
) -> bool:
    """A Gruppeadmin manages the groups they are assigned to and all subgroups."""
    seen: set[int] = set()
    current: int | None = group_id
    while current is not None and current not in seen:
        if current in admin_groups:
            return True
        seen.add(current)
        current = parent_of.get(current)
    return False


async def build_report(
    session, *, bar_names: Sequence[str], now: datetime, host: str | None = None
) -> ReadinessReport:
    """All reads. ``session`` is never written to."""
    semester = get_current_semester_code(now)
    report = ReadinessReport(host=host, semester=semester)
    today = now.astimezone(OSLO).date()
    semester_end = get_semester_end_date(semester)
    window_end = datetime.combine(
        semester_end + timedelta(days=1), time.min, tzinfo=OSLO
    )

    # --- global facts
    fake_people = await session.scalar(
        select(func.count())
        .select_from(volunteer_records)
        .where(func.lower(volunteer_records.c.email).like(f"%{FAKE_EMAIL_DOMAIN}"))
    )
    report.global_checks.append(
        Check(
            OK if not fake_people else CHECK,
            "fake people (@example.dev) in the database",
            str(fake_people or 0)
            + (
                ""
                if not fake_people
                else " (this is a rehearsal database, not a real one)"
            ),
        )
    )

    all_groups = (
        await session.execute(
            select(
                groups.c.id, groups.c.name, groups.c.parent_group_id, groups.c.is_active
            )
        )
    ).all()
    parent_of = {g.id: g.parent_group_id for g in all_groups}
    parent_id = next(
        (g.id for g in all_groups if g.name == PARENT_GROUP_NAME and g.is_active), None
    )
    report.global_checks.append(
        Check(
            OK if parent_id is not None else MISSING,
            f"group {PARENT_GROUP_NAME} exists and is active",
        )
    )

    # --- verv copy, once for all bars (same rules as the copy script)
    verv_rows, sets, targets = await copy.load_inputs(session)
    mapped = copy.map_verv(
        verv_rows,
        current_semester=semester,
        bar_group_ids=sets["bars"],
        kitchen_group_ids=sets["kitchens"],
        ancestor_group_ids=sets["ancestors"],
        training_targets=targets,
    )
    existing = {
        (v, q, g)
        for v, q, g in (
            await session.execute(
                select(
                    volunteer_qualifications.c.volunteer_id,
                    volunteer_qualifications.c.qualification,
                    volunteer_qualifications.c.group_id,
                )
            )
        ).all()
    }
    fresh_by_group: dict[int, int] = defaultdict(int)
    present_by_group: dict[int, int] = defaultdict(int)
    for row in mapped.rows:
        if (row.volunteer_id, row.qualification, row.group_id) in existing:
            present_by_group[row.group_id] += 1
        else:
            fresh_by_group[row.group_id] += 1

    # --- Gruppeadmin assignments: auth_user_id -> groups, and volunteer -> (role, auth user)
    admin_groups: dict[object, set[int]] = defaultdict(set)
    for auth_user_id, group_id in (
        await session.execute(
            select(
                group_admin_memberships.c.auth_user_id,
                group_admin_memberships.c.group_id,
            )
        )
    ).all():
        admin_groups[auth_user_id].add(group_id)
    accounts = {
        a.volunteer_id: a
        for a in (
            await session.execute(
                select(
                    user_accounts.c.volunteer_id,
                    user_accounts.c.auth_user_id,
                    user_accounts.c.role,
                ).where(user_accounts.c.volunteer_id.is_not(None))
            )
        ).all()
    }
    gruppeadmins = [
        a
        for a in (
            await session.execute(
                select(user_accounts.c.auth_user_id).where(
                    user_accounts.c.role == UserRole.GROUP_ADMIN.value
                )
            )
        ).all()
    ]

    # --- this semester's verv per group: volunteer ids by kind, plus emails
    verv_now = (
        await session.execute(
            select(
                role_assignments.c.group_id,
                role_assignments.c.volunteer_id,
                assignment_roles.c.name,
                volunteer_records.c.email,
            )
            .select_from(
                role_assignments.join(
                    volunteer_records,
                    volunteer_records.c.id == role_assignments.c.volunteer_id,
                ).outerjoin(
                    assignment_roles,
                    assignment_roles.c.id == role_assignments.c.role_id,
                )
            )
            .where(role_assignments.c.semester == semester)
        )
    ).all()

    by_name = defaultdict(list)
    for g in all_groups:
        if g.parent_group_id == parent_id and parent_id is not None:
            by_name[g.name].append(g)

    for name in bar_names:
        bar = BarReport(name=name)
        report.bars.append(bar)
        found = by_name.get(name, [])
        if not found:
            bar.checks.append(
                Check(
                    MISSING,
                    f"group exists under {PARENT_GROUP_NAME}",
                    "no group with this name",
                )
            )
            continue
        group = found[0]
        if len(found) > 1:
            bar.checks.append(
                Check(
                    CHECK,
                    "several groups with this name under the parent",
                    str(len(found)),
                )
            )
        gid = group.id
        bar.checks.append(
            Check(
                OK if group.is_active else MISSING,
                "group exists and is active",
                f"id {gid}",
            )
        )

        enabled = await session.scalar(
            select(shift_group_settings.c.app_enabled).where(
                shift_group_settings.c.group_id == gid
            )
        )
        bar.checks.append(
            Check(
                OK if enabled else MISSING,
                "participating-group switch (app_enabled) is on",
                "no settings row" if enabled is None else ("on" if enabled else "off"),
            )
        )

        templates = await session.scalar(
            select(func.count())
            .select_from(shift_templates)
            .where(
                shift_templates.c.group_id == gid, shift_templates.c.is_active.is_(True)
            )
        )
        bar.checks.append(_count_check("active shift templates", templates or 0))

        generated = await session.scalar(
            select(func.count())
            .select_from(shifts)
            .where(
                shifts.c.group_id == gid,
                shifts.c.starts_at >= now,
                shifts.c.starts_at < window_end,
            )
        )
        bar.checks.append(
            _count_check(
                f"shifts from now to the end of the semester ({semester_end.isoformat()})",
                generated or 0,
                hint="run the generator on /shifts/settings",
            )
        )

        closed = await session.scalar(
            select(func.count())
            .select_from(shift_closed_periods)
            .where(
                shift_closed_periods.c.group_id == gid,
                shift_closed_periods.c.ends_on >= today,
            )
        )
        bar.checks.append(
            Check(
                OK if closed else CHECK,
                "closed periods (holidays) not yet ended",
                str(closed or 0)
                + ("" if closed else " (is the semester break entered?)"),
            )
        )

        # verv this semester
        people: dict[str, set[int]] = defaultdict(set)
        everyone: set[int] = set()
        no_email: set[int] = set()
        for row in verv_now:
            if row.group_id != gid:
                continue
            key = copy.verv_key(row.name)
            kind = {
                copy._SKIFTLEDER_KEY: "Skiftleder",
                copy._OPPLAERING_KEY: "Opplæring",
                copy.verv_key("Kokk"): "Kokk",
            }.get(key, "other")
            people[kind].add(row.volunteer_id)
            everyone.add(row.volunteer_id)
            if not (row.email or "").strip():
                no_email.add(row.volunteer_id)
        bar.checks.append(
            _count_check("people with a verv this semester", len(everyone))
        )
        # A kitchen is run by Kokk; a bar by Skiftleder.
        is_kitchen = gid in sets["kitchens"]
        bar.checks.append(
            Check(
                OK if people["Skiftleder"] else (CHECK if is_kitchen else MISSING),
                "people with verv Skiftleder this semester",
                str(len(people["Skiftleder"])),
            )
        )
        bar.checks.append(
            Check(
                (OK if people["Kokk"] else MISSING) if is_kitchen else CHECK,
                "people with verv Kokk this semester",
                str(len(people["Kokk"])),
            )
        )
        bar.checks.append(
            Check(
                CHECK,
                "people with verv Opplæring this semester",
                str(len(people["Opplæring"])),
            )
        )
        bar.checks.append(
            Check(
                CHECK,
                "people with another verv this semester",
                str(len(people["other"])),
            )
        )

        recognised = gid in sets["bars"] or gid in sets["kitchens"]
        bar.checks.append(
            Check(
                OK if recognised else MISSING,
                "the verv copy script treats this group as a bar or the kitchen",
                "" if recognised else "needs templates with skjenker or kokk places",
            )
        )
        bar.checks.append(
            Check(
                CHECK,
                "qualification rows the verv copy script would add",
                f"{fresh_by_group.get(gid, 0)} (already present: {present_by_group.get(gid, 0)})",
            )
        )

        bar.checks.append(
            Check(
                OK if not no_email else MISSING,
                "people with a verv here and no email",
                str(len(no_email)),
            )
        )

        # skiftledere and their access
        leaders = people["Skiftleder"]
        no_login = 0
        no_assignment = 0
        for volunteer_id in leaders:
            account = accounts.get(volunteer_id)
            if account is None:
                no_login += 1
                continue
            is_admin = account.role == UserRole.ADMIN.value
            covered = account.role == UserRole.GROUP_ADMIN.value and gruppeadmin_covers(
                gid, admin_groups.get(account.auth_user_id, set()), parent_of
            )
            if not (is_admin or covered):
                no_assignment += 1
        bar.checks.append(
            Check(
                OK
                if leaders and not no_login and not no_assignment
                else MISSING
                if leaders
                else CHECK,
                "skiftledere without a login / without Gruppeadmin for this bar",
                f"{no_login} without a login, {no_assignment} with a login but no Gruppeadmin "
                f"covering this bar (of {len(leaders)})",
            )
        )
        any_admin = sum(
            1
            for a in gruppeadmins
            if gruppeadmin_covers(
                gid, admin_groups.get(a.auth_user_id, set()), parent_of
            )
        )
        bar.checks.append(
            _count_check(
                "Gruppeadmin accounts covering this bar",
                any_admin,
                hint="a leader needs this to open the bar in the app",
            )
        )
    return report


def format_report(report: ReadinessReport) -> str:
    lines = [
        "[readiness] READ ONLY: nothing was written"
        + (f" ({report.host})" if report.host else ""),
        f"[readiness] semester code {report.semester}",
    ]
    for check in report.global_checks:
        lines.append(_line(check))
    for bar in report.bars:
        lines.append(f"[readiness] --- {bar.name}")
        lines.extend(_line(check) for check in bar.checks)
    missing = report.missing_count()
    lines.append(
        f"[readiness] {missing} item(s) MISSING"
        if missing
        else "[readiness] nothing is MISSING"
    )
    return "\n".join(lines)


def _line(check: Check) -> str:
    tail = f": {check.detail}" if check.detail else ""
    return f"[readiness] {check.status:<7} {check.label}{tail}"


async def pilot_readiness(
    database_url: str, *, bar_names: Sequence[str], now: datetime | None = None
) -> ReadinessReport:
    runtime = build_database_runtime(
        Settings(database_url=database_url, app_env="development")
    )
    try:
        async with session_scope(runtime) as session:
            if make_url(database_url).get_backend_name() == "postgresql":
                await session.execute(text("SET TRANSACTION READ ONLY"))
            try:
                report = await build_report(
                    session,
                    bar_names=bar_names,
                    now=now or datetime.now(UTC),
                    host=make_url(database_url).host,
                )
            finally:
                await session.rollback()
    finally:
        await runtime.aclose()
    print(format_report(report))
    return report


def main(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--bars",
        default=",".join(DEFAULT_BARS),
        help="comma separated group names under Skjenkegruppen",
    )
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 when something is MISSING"
    )
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    url = env.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    bars = [b.strip() for b in args.bars.split(",") if b.strip()]
    report = asyncio.run(pilot_readiness(url, bar_names=bars))
    return 1 if args.strict and report.missing_count() else 0


if __name__ == "__main__":
    raise SystemExit(main())
