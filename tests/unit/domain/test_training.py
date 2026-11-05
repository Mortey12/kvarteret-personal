"""Training on the person: counted from finished shifts, ready at the required number."""

from datetime import UTC, datetime, timedelta

from app.domain.shifts.models import QualificationRecord, QualificationSettings
from app.domain.shifts.qualification_types import QualificationType
from app.domain.shifts.training import TrainingRecord, place_text, progress, training_records

START = datetime(2026, 11, 1, tzinfo=UTC)
NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


def record(required: int = 3, qualification: str = "skjenker") -> TrainingRecord:
    return TrainingRecord(7, 5, qualification, required, START)


def shift(day: int) -> tuple[datetime, datetime]:
    begin = START + timedelta(days=day, hours=16)
    return begin, begin + timedelta(hours=6)


def test_counts_only_finished_shifts_after_training_started() -> None:
    attended = [shift(-3), shift(2), shift(5), shift(30)]  # before, two done, one in the future
    result = progress(record(), attended, NOW)
    assert (result.done, result.required, result.ready) == (2, 3, False)
    assert result.text == "opplæring 2/3"


def test_ready_when_enough_are_done() -> None:
    attended = [shift(2), shift(5), shift(8)]
    assert progress(record(), attended, NOW).ready is True
    assert progress(record(), attended[:2], NOW).ready is False


def test_one_more_shift_makes_them_not_ready_again() -> None:
    attended = [shift(2), shift(5), shift(8)]
    assert progress(record(required=4), attended, NOW).ready is False
    assert progress(record(required=4), attended + [shift(10)], NOW).ready is True


def test_text_on_a_place_numbers_the_training_shift() -> None:
    attended = [shift(2), shift(5), shift(25)]
    assert place_text(record(), attended, shift(2)[0]) == "opplæring 1/3"
    assert place_text(record(), attended, shift(5)[0]) == "opplæring 2/3"
    assert place_text(record(), attended, shift(25)[0]) == "opplæring 3/3"
    assert place_text(record(), attended, shift(-2)[0]) is None


def test_only_training_rows_of_this_bar_count_and_the_target_wins_over_the_bar_setting() -> None:
    def q(who, status, group, target=None, qualification="skjenker"):
        return QualificationRecord(who, qualification, status, group, target, False, None, None, START)

    rows = [
        q(1, "training", 5),
        q(2, "approved", 5),
        q(3, "training", 6),
        q(4, "training", 5, 4, "skiftleder"),
        q(5, "training", 5, None, "ukjent"),  # a qualification that is not a known type is left out
    ]
    types = {
        "skjenker": QualificationType(1, "skjenker", "skjenker", "normal", True, None, 0),
        "skiftleder": QualificationType(2, "skiftleder", "skiftleder", "skiftleder", True, None, 1),
    }
    found = training_records(rows, QualificationSettings(training_shifts_needed=3), 5, types)
    assert [(r.volunteer_id, r.required, r.kind, r.label) for r in found] == [
        (1, 3, "normal", "skjenker"),
        (4, 4, "skiftleder", "skiftleder"),
    ]
