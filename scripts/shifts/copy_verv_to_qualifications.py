"""One-time copy of verv into ``volunteer_qualifications`` (Skiftlista 5d).

Run it after the bars and the kitchen have templates or settings rows. It
reads ``DATABASE_URL``. The default is a dry run: nothing is written, it prints
the summary and the check list. ``--apply`` writes in one transaction, and only
when ``COPY_CONFIRM`` equals the host name in ``DATABASE_URL``. Running it again
adds nothing (the unique key is respected).

    uv run python scripts/shifts/copy_verv_to_qualifications.py
    COPY_CONFIRM=<host> uv run python scripts/shifts/copy_verv_to_qualifications.py --apply

Rules (decided 2026-10-29), over all semesters unless stated:

- Groups: active groups with templates or a settings row of their own. A bar
  has skjenker or bartender places in its templates; the kitchen has kokk places
  and no bar places.
- skjenker approved: anyone who ever held a verv other than Opplæring (or no
  verv) in a bar. One row per person, on the bar of their most recent such
  semester (ties: lowest group id). Never on a parent, never from the kitchen.
- skiftleder approved: anyone who ever held Skiftleder in a bar, one row per bar.
- kokk approved: anyone who ever held a verv other than Opplæring in the kitchen.
- Training rows: only from an Opplæring verv in the current semester (kokk in
  the kitchen, skjenker in a bar), dropped when the person is already approved
  for that qualification. ``training_target`` is the group's
  ``training_shifts_needed``.
- ``takes_trainees`` on skiftleder and kokk rows when the person held that role
  in the current or the previous semester, else off.
- ``approved_by`` is null and ``approved_at`` is the run time.

The check list holds volunteer ids only, no names.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import insert, select
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db.session import build_database_runtime, session_scope
from app.domain.groups.tables import groups
from app.domain.role_assignments.tables import assignment_roles, role_assignments
from app.domain.shifts.tables import (
    shift_group_settings,
    shift_template_slots,
    shift_templates,
    volunteer_qualifications,
)
from app.shared.semester import get_current_semester_code, get_previous_semester_code

# The verv names the copy reads. Every other name is printed as unrecognised.
VERV_SKIFTLEDER = "Skiftleder"
VERV_OPPLAERING = "Opplæring"
RECOGNISED_VERV = (VERV_SKIFTLEDER, VERV_OPPLAERING)

# Used when a group has no settings row (the column's default).
DEFAULT_TRAINING_TARGET = 3

BAR_KINDS = frozenset({"skjenker", "bartender"})


class CopyRefused(Exception):
    """The run was refused before it touched the database."""


@dataclass(frozen=True, slots=True)
class VervRow:
    """One role assignment (``verv_name`` is None when the role has no verv)."""

    volunteer_id: int
    group_id: int
    semester: int
    verv_name: str | None


@dataclass(frozen=True, slots=True)
class QualificationRow:
    volunteer_id: int
    qualification: str
    status: str
    group_id: int
    takes_trainees: bool
    training_target: int | None


@dataclass(slots=True)
class MappingResult:
    rows: list[QualificationRow] = field(default_factory=list)
    # Verv name (as written, trimmed) -> how many assignments carried it.
    unrecognised: dict[str, int] = field(default_factory=dict)
    # Volunteer ids for the check list.
    training_people: list[int] = field(default_factory=list)
    returning_leaders: list[int] = field(default_factory=list)
    people_in_several_bars: list[int] = field(default_factory=list)
    ancestor_only_people: list[int] = field(default_factory=list)


def verv_key(name: str | None) -> str | None:
    cleaned = (name or "").strip().casefold()
    return cleaned or None


_SKIFTLEDER_KEY = verv_key(VERV_SKIFTLEDER)
_OPPLAERING_KEY = verv_key(VERV_OPPLAERING)


def is_bar_group(kinds: set[str] | frozenset[str]) -> bool:
    return bool(set(kinds) & BAR_KINDS)


def is_kitchen_group(kinds: set[str] | frozenset[str]) -> bool:
    """Templates with kokk places and no bar places."""
    return "kokk" in kinds and not is_bar_group(kinds)


def map_verv(
    rows: Sequence[VervRow],
    *,
    current_semester: int,
    bar_group_ids: frozenset[int],
    kitchen_group_ids: frozenset[int],
    ancestor_group_ids: frozenset[int] = frozenset(),
    training_targets: Mapping[int, int] | None = None,
) -> MappingResult:
    """Turn verv into qualification rows. Pure: no database.

    ``rows`` are the role assignments of all semesters in the bars, the kitchen
    and the non-bar ancestors. Groups outside those sets give no rows.
    """
    targets = training_targets or {}
    recent = {current_semester, get_previous_semester_code(current_semester)}
    result = MappingResult()

    # (volunteer) -> (semester, -group) of the most recent skjenker role, per bar
    skjenker_best: dict[int, tuple[int, int]] = {}
    # (volunteer, group) -> latest semester of that role
    skiftleder_latest: dict[tuple[int, int], int] = {}
    kokk_latest: dict[tuple[int, int], int] = {}
    training: list[tuple[int, int, bool]] = []  # volunteer, group, is_kitchen
    bars_now: dict[int, set[int]] = defaultdict(set)
    kitchen_now: set[int] = set()
    ancestor_now: set[int] = set()

    for row in rows:
        in_bar = row.group_id in bar_group_ids
        in_kitchen = row.group_id in kitchen_group_ids
        if not (in_bar or in_kitchen or row.group_id in ancestor_group_ids):
            continue
        key = verv_key(row.verv_name)
        if in_bar or in_kitchen:
            if key not in (_SKIFTLEDER_KEY, _OPPLAERING_KEY) and key is not None:
                name = (row.verv_name or "").strip()
                result.unrecognised[name] = result.unrecognised.get(name, 0) + 1
        if row.semester == current_semester:
            if in_bar:
                bars_now[row.volunteer_id].add(row.group_id)
            elif in_kitchen:
                kitchen_now.add(row.volunteer_id)
            else:
                ancestor_now.add(row.volunteer_id)
        if not (in_bar or in_kitchen):
            continue
        if key == _OPPLAERING_KEY:
            if row.semester == current_semester:
                training.append((row.volunteer_id, row.group_id, in_kitchen))
            continue
        if in_kitchen:
            _keep_latest(kokk_latest, (row.volunteer_id, row.group_id), row.semester)
            continue
        best = (row.semester, -row.group_id)
        if row.volunteer_id not in skjenker_best or best > skjenker_best[row.volunteer_id]:
            skjenker_best[row.volunteer_id] = best
        if key == _SKIFTLEDER_KEY:
            _keep_latest(skiftleder_latest, (row.volunteer_id, row.group_id), row.semester)

    approved: list[QualificationRow] = []
    for volunteer_id, (_, negative_group) in skjenker_best.items():
        approved.append(_approved(volunteer_id, "skjenker", -negative_group, False))
    for (volunteer_id, group_id), latest in skiftleder_latest.items():
        approved.append(_approved(volunteer_id, "skiftleder", group_id, latest in recent))
    for (volunteer_id, group_id), latest in kokk_latest.items():
        approved.append(_approved(volunteer_id, "kokk", group_id, latest in recent))
    approved.sort(key=lambda r: (r.volunteer_id, r.qualification, r.group_id))
    result.rows.extend(approved)

    has_skjenker = {r.volunteer_id for r in approved if r.qualification == "skjenker"}
    has_kokk = {(r.volunteer_id, r.group_id) for r in approved if r.qualification == "kokk"}
    trained: set[tuple[int, str, int]] = set()
    for volunteer_id, group_id, in_kitchen in sorted(set(training)):
        qualification = "kokk" if in_kitchen else "skjenker"
        if qualification == "kokk" and (volunteer_id, group_id) in has_kokk:
            continue
        if qualification == "skjenker" and volunteer_id in has_skjenker:
            continue
        if (volunteer_id, qualification, group_id) in trained:
            continue
        trained.add((volunteer_id, qualification, group_id))
        result.rows.append(
            QualificationRow(
                volunteer_id=volunteer_id,
                qualification=qualification,
                status="training",
                group_id=group_id,
                takes_trainees=False,
                training_target=targets.get(group_id, DEFAULT_TRAINING_TARGET),
            )
        )

    result.training_people = sorted({r.volunteer_id for r in result.rows if r.status == "training"})
    result.returning_leaders = sorted(
        {
            r.volunteer_id
            for r in result.rows
            if r.status == "approved"
            and r.qualification in {"skiftleder", "kokk"}
            and not r.takes_trainees
        }
    )
    result.people_in_several_bars = sorted(v for v, bars in bars_now.items() if len(bars) > 1)
    given = {r.volunteer_id for r in result.rows}
    result.ancestor_only_people = sorted(
        volunteer_id
        for volunteer_id in ancestor_now
        if volunteer_id not in given
        and volunteer_id not in kitchen_now
        and not bars_now.get(volunteer_id)
    )
    return result


def _keep_latest(latest: dict[tuple[int, int], int], key: tuple[int, int], semester: int) -> None:
    if semester > latest.get(key, 0):
        latest[key] = semester


def _approved(
    volunteer_id: int, qualification: str, group_id: int, takes_trainees: bool
) -> QualificationRow:
    return QualificationRow(
        volunteer_id=volunteer_id,
        qualification=qualification,
        status="approved",
        group_id=group_id,
        takes_trainees=takes_trainees,
        training_target=None,
    )


# ------------------------------------------------------------------ database


def database_host(database_url: str) -> str | None:
    return make_url(database_url).host


def check_apply_target(database_url: str, confirm: str | None) -> str:
    """The host to write to, or a refusal. ``--apply`` needs COPY_CONFIRM=<host>."""
    host = database_host(database_url)
    if not host:
        raise CopyRefused("DATABASE_URL has no host name, so it cannot be confirmed.")
    if confirm != host:
        raise CopyRefused(
            f"--apply writes to {host}. To do that on purpose, set COPY_CONFIRM={host}."
        )
    return host


@dataclass(slots=True)
class CopyReport:
    host: str | None
    applied: bool
    result: MappingResult
    new_rows: int
    existing_rows: int


async def load_inputs(session) -> tuple[list[VervRow], dict, dict[int, int]]:
    """Role rows plus the group sets ``map_verv`` needs, from active groups only."""
    group_rows = (
        await session.execute(
            select(groups.c.id, groups.c.parent_group_id, groups.c.is_active)
        )
    ).all()
    parent_of = {g.id: g.parent_group_id for g in group_rows}
    active = {g.id for g in group_rows if g.is_active}

    kinds: dict[int, set[str]] = defaultdict(set)
    for group_id, kind in (
        await session.execute(
            select(shift_templates.c.group_id, shift_template_slots.c.kind).join(
                shift_template_slots, shift_template_slots.c.template_id == shift_templates.c.id
            )
        )
    ).all():
        kinds[group_id].add(kind)
    templated = set(
        (await session.execute(select(shift_templates.c.group_id))).scalars().all()
    )
    targets = {
        group_id: needed
        for group_id, needed in (
            await session.execute(
                select(
                    shift_group_settings.c.group_id, shift_group_settings.c.training_shifts_needed
                )
            )
        ).all()
    }
    own = {
        group_id
        for group_id in templated | set(targets)
        if group_id in active
    }
    bars = frozenset(g for g in own if is_bar_group(kinds.get(g, set())))
    kitchens = frozenset(g for g in own if is_kitchen_group(kinds.get(g, set())))

    ancestors: set[int] = set()
    for group_id in own:
        seen: set[int] = {group_id}
        parent = parent_of.get(group_id)
        while parent is not None and parent not in seen:
            seen.add(parent)
            if parent in active:
                ancestors.add(parent)
            parent = parent_of.get(parent)
    ancestors -= bars | kitchens

    wanted = bars | kitchens | ancestors
    verv_rows: list[VervRow] = []
    if wanted:
        verv_rows = [
            VervRow(
                volunteer_id=r.volunteer_id,
                group_id=r.group_id,
                semester=r.semester,
                verv_name=r.name,
            )
            for r in (
                await session.execute(
                    select(
                        role_assignments.c.volunteer_id,
                        role_assignments.c.group_id,
                        role_assignments.c.semester,
                        assignment_roles.c.name,
                    )
                    .select_from(
                        role_assignments.outerjoin(
                            assignment_roles, assignment_roles.c.id == role_assignments.c.role_id
                        )
                    )
                    .where(role_assignments.c.group_id.in_(wanted))
                    .order_by(role_assignments.c.id)
                )
            ).all()
        ]
    sets = {"bars": bars, "kitchens": kitchens, "ancestors": frozenset(ancestors)}
    return verv_rows, sets, targets


async def run_copy(
    session, *, apply: bool, now: datetime, host: str | None = None
) -> CopyReport:
    verv_rows, sets, targets = await load_inputs(session)
    result = map_verv(
        verv_rows,
        current_semester=get_current_semester_code(now),
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
    fresh = [r for r in result.rows if (r.volunteer_id, r.qualification, r.group_id) not in existing]
    if apply and fresh:
        await session.execute(
            insert(volunteer_qualifications),
            [
                {
                    "volunteer_id": r.volunteer_id,
                    "qualification": r.qualification,
                    "status": r.status,
                    "group_id": r.group_id,
                    "training_target": r.training_target,
                    "takes_trainees": r.takes_trainees,
                    "approved_by": None,
                    "approved_at": now if r.status == "approved" else None,
                }
                for r in fresh
            ],
        )
    return CopyReport(
        host=host,
        applied=apply,
        result=result,
        new_rows=len(fresh),
        existing_rows=len(result.rows) - len(fresh),
    )


def format_report(report: CopyReport) -> str:
    result = report.result
    approved = sum(1 for r in result.rows if r.status == "approved")
    lines = [
        f"[copy-verv] {'APPLIED' if report.applied else 'DRY RUN (nothing written)'}"
        + (f" on {report.host}" if report.host else ""),
        f"[copy-verv] qualification rows wanted: {approved} approved, "
        f"{len(result.rows) - approved} training",
        f"[copy-verv] {'added' if report.applied else 'would add'}: {report.new_rows}; "
        f"already present: {report.existing_rows}",
        f"[copy-verv] recognised verv names: {', '.join(RECOGNISED_VERV)}",
    ]
    if result.unrecognised:
        lines.append("[copy-verv] verv names not recognised (they count as other verv):")
        lines.extend(
            f"[copy-verv]   {name!r}: {count}" for name, count in sorted(result.unrecognised.items())
        )
    else:
        lines.append("[copy-verv] every verv name was recognised")
    lines += [
        f"[copy-verv] check: training rows (volunteer ids): {result.training_people}",
        "[copy-verv] check: returning people with skiftleder or kokk rows "
        f"(volunteer ids): {result.returning_leaders}",
        f"[copy-verv] check: roles in several bars this semester (volunteer ids): "
        f"{result.people_in_several_bars}",
        "[copy-verv] check: only a role in a participating parent, no row, needs "
        f"'Godkjenn direkte' (volunteer ids): {result.ancestor_only_people}",
    ]
    return "\n".join(lines)


async def copy_verv(
    database_url: str, *, apply: bool, env: Mapping[str, str] | None = None
) -> CopyReport:
    env = os.environ if env is None else env
    host = database_host(database_url)
    if apply:
        host = check_apply_target(database_url, env.get("COPY_CONFIRM"))
    runtime = build_database_runtime(Settings(database_url=database_url, app_env="development"))
    try:
        async with session_scope(runtime) as session:
            report = await run_copy(session, apply=apply, now=datetime.now(UTC), host=host)
    finally:
        await runtime.aclose()
    print(format_report(report))
    return report


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the rows (needs COPY_CONFIRM=<host of DATABASE_URL>); default is a dry run",
    )
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    url = env.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    try:
        asyncio.run(copy_verv(url, apply=args.apply, env=env))
    except CopyRefused as refused:
        print(f"Refused: {refused}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
