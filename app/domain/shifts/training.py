"""Training kept on the person, counted from finished shifts.

Training is not a kind of place. A person is in training when they hold a qualification with
status "training" in a bar (``volunteer_qualifications``): "skjenker" or "kokk" for a normal
training, "skiftleder" for skiftleder training. It takes ``required`` shifts: the person's own
``training_target`` when a leader has given more, otherwise the bar's setting (3).

How many are done is counted, never typed: the shifts the person was on in that bar that ended
after training started, not counting a shift they said "kan ikke i dag" for. When the count reaches
``required`` the person is *ready*: a leader approves them (the qualification becomes "approved")
or gives them one more shift (``training_target`` goes up by one). That is all there is to it.

A skiftleder in training needs an experienced skiftleder on the same shift, unless the leader
decides otherwise: that is a warning when placing, never a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.shifts.models import QualificationRecord, QualificationSettings

TRAINEE_QUALIFICATIONS = ("skjenker", "kokk", "skiftleder")


@dataclass(frozen=True, slots=True)
class TrainingRecord:
    volunteer_id: int
    group_id: int
    qualification: str
    required: int
    started_at: datetime

    @property
    def kind(self) -> str:
        return "skiftleder" if self.qualification == "skiftleder" else "normal"


@dataclass(frozen=True, slots=True)
class TrainingProgress:
    qualification: str
    kind: str
    done: int
    required: int
    # Enough shifts are done and nobody has approved or extended yet: the leader has to act.
    ready: bool

    @property
    def text(self) -> str:
        """"opplæring 2/3" for a tag."""
        return f"opplæring {min(self.done, self.required)}/{self.required}"


def training_records(
    qualifications: list[QualificationRecord], settings: QualificationSettings, group_id: int
) -> list[TrainingRecord]:
    """Everyone in training in this bar."""
    return [
        TrainingRecord(
            volunteer_id=q.volunteer_id,
            group_id=q.group_id,
            qualification=q.qualification,
            required=q.training_target or settings.training_shifts_needed,
            started_at=q.created_at or datetime.min.replace(tzinfo=UTC),
        )
        for q in qualifications
        if q.status == "training" and q.group_id == group_id and q.qualification in TRAINEE_QUALIFICATIONS
    ]


def done_shifts(record: TrainingRecord, attended: list[tuple[datetime, datetime]], now: datetime) -> int:
    """Shifts finished since training started. ``attended`` is (starts_at, ends_at) of each shift
    the person was on in this bar, without the ones marked "kan ikke i dag"."""
    return sum(1 for starts_at, ends_at in attended if ends_at <= now and starts_at >= record.started_at)


def progress(record: TrainingRecord, attended: list[tuple[datetime, datetime]], now: datetime) -> TrainingProgress:
    done = done_shifts(record, attended, now)
    return TrainingProgress(
        qualification=record.qualification,
        kind=record.kind,
        done=done,
        required=record.required,
        ready=done >= record.required,
    )


def place_text(record: TrainingRecord, attended: list[tuple[datetime, datetime]], shift_starts_at: datetime) -> str | None:
    """"opplæring 2/3" for one place: which of the training shifts this one is. None for a shift
    before training started."""
    if shift_starts_at < record.started_at:
        return None
    number = sum(1 for starts_at, _ in attended if record.started_at <= starts_at <= shift_starts_at)
    return f"opplæring {min(max(number, 1), record.required)}/{record.required}"
