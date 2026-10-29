"""Skiftlista 5d, migration A: qualifications and training, additive only.

Creates ``volunteer_qualifications`` and the columns the new code needs,
then copies the verv of the current and the previous semester into
qualification rows (see ``map_verv``). Nothing is removed or moved here: the
``opplaering`` slots and ``training_replaces_slot`` stay until the later
revisions that go with step 3 of 5d.

The migration prints every verv name it did not recognise and a check list
(volunteer ids only), so a run on real data shows misses instead of silently
giving people no qualification.

Revision ID: 20261101_1200
Revises: 20261031_1200
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261101_1200"
down_revision = "20261031_1200"
branch_labels = None
depends_on = None

# The verv names the copy reads. Anything else counts as "other verv", is
# printed as unrecognised, and gives a skjenker qualification.
VERV_SKIFTLEDER = "Skiftleder"
VERV_KOKK = "Kokk"
VERV_OPPLAERING = "Opplæring"
RECOGNISED_VERV = (VERV_SKIFTLEDER, VERV_KOKK, VERV_OPPLAERING)

# Copied into a training row, like the bar's setting does when training starts.
DEFAULT_TRAINING_TARGET = 3

_BAR_KINDS = frozenset({"skjenker", "bartender"})


@dataclass(frozen=True, slots=True)
class VervRow:
    """One role assignment in a participating group."""

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
    # Verv name (as written) -> how many assignments carried it.
    unrecognised: dict[str, int] = field(default_factory=dict)
    # Volunteer ids for the check list.
    opplaering_people: list[int] = field(default_factory=list)
    kitchen_members_without_kokk: list[int] = field(default_factory=list)
    people_in_several_groups: list[int] = field(default_factory=list)


def _verv_key(name: str | None) -> str | None:
    cleaned = (name or "").strip().casefold()
    return cleaned or None


_RECOGNISED_KEYS = {_verv_key(name): name for name in RECOGNISED_VERV}


def previous_semester(semester: int) -> int:
    year, season = divmod(semester, 10)
    return year * 10 + 1 if season == 2 else (year - 1) * 10 + 2


def is_kitchen_group(kinds: set[str] | frozenset[str]) -> bool:
    """Templates with kokk places and no bar places."""
    return "kokk" in kinds and not (set(kinds) & _BAR_KINDS)


def map_verv(
    rows: list[VervRow],
    *,
    current_semester: int,
    kitchen_group_ids: frozenset[int] = frozenset(),
) -> MappingResult:
    """Turn verv into qualification rows. Pure: no database.

    Only the current and the previous semester count. ``rows`` should be in
    the order "first group wins" for skjenker (the migration sorts by newest
    semester, then group id). Skiftleder and kokk rows are kept per group, a
    skjenker row once per person. Approved wins over training for the same
    qualification.
    """
    wanted = {current_semester, previous_semester(current_semester)}
    result = MappingResult()
    approved: dict[tuple[int, str, int], QualificationRow] = {}
    skjenker_given: set[int] = set()
    training: list[VervRow] = []
    groups_of: dict[int, set[int]] = defaultdict(set)
    kokk_people: set[int] = set()
    kitchen_people: set[int] = set()

    for row in rows:
        if row.semester not in wanted:
            continue
        groups_of[row.volunteer_id].add(row.group_id)
        if row.group_id in kitchen_group_ids:
            kitchen_people.add(row.volunteer_id)
        key = _verv_key(row.verv_name)
        recognised = _RECOGNISED_KEYS.get(key)
        if row.verv_name and key and recognised is None:
            result.unrecognised[row.verv_name.strip()] = (
                result.unrecognised.get(row.verv_name.strip(), 0) + 1
            )
        if recognised == VERV_OPPLAERING:
            training.append(row)
            continue
        if recognised == VERV_KOKK:
            kokk_people.add(row.volunteer_id)
            _add_approved(approved, row, "kokk", takes_trainees=True)
            continue
        if recognised == VERV_SKIFTLEDER:
            _add_approved(approved, row, "skiftleder", takes_trainees=True)
        if row.volunteer_id not in skjenker_given:
            skjenker_given.add(row.volunteer_id)
            _add_approved(approved, row, "skjenker", takes_trainees=False)

    result.rows.extend(approved.values())
    trained: set[tuple[int, str, int]] = set()
    for row in training:
        qualification = "kokk" if row.group_id in kitchen_group_ids else "skjenker"
        key = (row.volunteer_id, qualification, row.group_id)
        already = key in approved or key in trained
        if qualification == "skjenker" and row.volunteer_id in skjenker_given:
            already = True
        if row.volunteer_id not in result.opplaering_people:
            result.opplaering_people.append(row.volunteer_id)
        if already:
            continue
        trained.add(key)
        result.rows.append(
            QualificationRow(
                volunteer_id=row.volunteer_id,
                qualification=qualification,
                status="training",
                group_id=row.group_id,
                takes_trainees=False,
                training_target=DEFAULT_TRAINING_TARGET,
            )
        )
    result.kitchen_members_without_kokk = sorted(kitchen_people - kokk_people)
    result.people_in_several_groups = sorted(
        volunteer_id for volunteer_id, group_ids in groups_of.items() if len(group_ids) > 1
    )
    return result


def _add_approved(
    approved: dict[tuple[int, str, int], QualificationRow],
    row: VervRow,
    qualification: str,
    *,
    takes_trainees: bool,
) -> None:
    key = (row.volunteer_id, qualification, row.group_id)
    if key in approved:
        return
    approved[key] = QualificationRow(
        volunteer_id=row.volunteer_id,
        qualification=qualification,
        status="approved",
        group_id=row.group_id,
        takes_trainees=takes_trainees,
        training_target=None,
    )


def _current_semester() -> int:
    now = datetime.now(UTC)
    return now.year * 10 + (1 if now.month <= 6 else 2)


def upgrade() -> None:
    op.create_table(
        "volunteer_qualifications",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "volunteer_id",
            sa.BigInteger(),
            sa.ForeignKey("public.volunteer_records.id"),
            nullable=False,
        ),
        sa.Column("qualification", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "group_id", sa.BigInteger(), sa.ForeignKey("public.groups.id"), nullable=False
        ),
        sa.Column("training_target", sa.Integer(), nullable=True),
        sa.Column("takes_trainees", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "volunteer_id",
            "qualification",
            "group_id",
            name="volunteer_qualifications_volunteer_qualification_group_key",
        ),
        sa.CheckConstraint(
            "qualification IN ('skjenker', 'skiftleder', 'kokk')",
            name="volunteer_qualifications_qualification_valid",
        ),
        sa.CheckConstraint(
            "status IN ('training', 'approved')",
            name="volunteer_qualifications_status_valid",
        ),
        schema="public",
    )
    op.create_index(
        "volunteer_qualifications_group_idx",
        "volunteer_qualifications",
        ["group_id"],
        schema="public",
    )
    op.execute("ALTER TABLE public.volunteer_qualifications ENABLE ROW LEVEL SECURITY")
    op.execute(
        "REVOKE ALL ON public.volunteer_qualifications FROM PUBLIC, anon, authenticated"
    )

    op.add_column(
        "shift_assignments", sa.Column("training_for", sa.Text(), nullable=True), schema="public"
    )
    op.add_column(
        "shift_group_settings",
        sa.Column(
            "training_shifts_needed", sa.Integer(), nullable=False, server_default="3"
        ),
        schema="public",
    )
    op.add_column(
        "shift_group_settings",
        sa.Column(
            "sibling_skiftleder_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="public",
    )
    # Already true from the first shift migration; stated again so the new code
    # may insert without the column before migration B drops it.
    for table_name in ("shift_templates", "shifts"):
        op.alter_column(
            table_name,
            "training_replaces_slot",
            server_default=sa.true(),
            schema="public",
        )

    _copy_verv()


def _copy_verv() -> None:
    bind = op.get_bind()
    current = _current_semester()
    in_scope = """
        ra.group_id IN (
            SELECT group_id FROM public.shift_templates
            UNION SELECT group_id FROM public.shift_group_settings
        )
    """
    verv_rows = [
        VervRow(
            volunteer_id=row.volunteer_id,
            group_id=row.group_id,
            semester=row.semester,
            verv_name=row.name,
        )
        for row in bind.execute(
            sa.text(
                f"""
                SELECT ra.volunteer_id, ra.group_id, ra.semester, ar.name
                FROM public.role_assignments ra
                LEFT JOIN public.assignment_roles ar ON ar.id = ra.role_id
                WHERE ra.semester IN (:current, :previous) AND {in_scope}
                ORDER BY ra.semester DESC, ra.group_id, ra.id
                """
            ),
            {"current": current, "previous": previous_semester(current)},
        )
    ]
    kinds_by_group: dict[int, set[str]] = defaultdict(set)
    for row in bind.execute(
        sa.text(
            """
            SELECT t.group_id, s.kind
            FROM public.shift_template_slots s
            JOIN public.shift_templates t ON t.id = s.template_id
            """
        )
    ):
        kinds_by_group[row.group_id].add(row.kind)
    kitchen = frozenset(
        group_id for group_id, kinds in kinds_by_group.items() if is_kitchen_group(kinds)
    )

    result = map_verv(verv_rows, current_semester=current, kitchen_group_ids=kitchen)

    if result.rows:
        approved_at = datetime.now(UTC)
        bind.execute(
            sa.text(
                """
                INSERT INTO public.volunteer_qualifications
                    (volunteer_id, qualification, status, group_id, training_target,
                     takes_trainees, approved_at)
                VALUES
                    (:volunteer_id, :qualification, :status, :group_id, :training_target,
                     :takes_trainees, :approved_at)
                """
            ),
            [
                {
                    "volunteer_id": row.volunteer_id,
                    "qualification": row.qualification,
                    "status": row.status,
                    "group_id": row.group_id,
                    "training_target": row.training_target,
                    "takes_trainees": row.takes_trainees,
                    "approved_at": approved_at if row.status == "approved" else None,
                }
                for row in result.rows
            ],
        )
    _print_report(result)


def _print_report(result: MappingResult) -> None:
    approved = sum(1 for row in result.rows if row.status == "approved")
    print(
        f"[skiftlista] qualification rows: {approved} approved, "
        f"{len(result.rows) - approved} training"
    )
    print(f"[skiftlista] recognised verv names: {', '.join(RECOGNISED_VERV)}")
    if result.unrecognised:
        print("[skiftlista] verv names not recognised (they gave skjenker only):")
        for name, count in sorted(result.unrecognised.items()):
            print(f"[skiftlista]   {name!r}: {count}")
    else:
        print("[skiftlista] every verv name was recognised")
    print(f"[skiftlista] check: Opplæring volunteer ids: {result.opplaering_people}")
    print(
        "[skiftlista] check: kitchen members without Kokk (volunteer ids): "
        f"{result.kitchen_members_without_kokk}"
    )
    print(
        "[skiftlista] check: volunteer ids in several groups: "
        f"{result.people_in_several_groups}"
    )


def downgrade() -> None:
    op.drop_column("shift_group_settings", "sibling_skiftleder_allowed", schema="public")
    op.drop_column("shift_group_settings", "training_shifts_needed", schema="public")
    op.drop_column("shift_assignments", "training_for", schema="public")
    op.drop_table("volunteer_qualifications", schema="public")
