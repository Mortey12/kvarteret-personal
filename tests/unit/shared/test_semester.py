import pytest

from app.shared.semester import get_next_semester_code, get_previous_semester_code


def test_previous_semester_steps_back_across_years() -> None:
    assert get_previous_semester_code(20262) == 20261
    assert get_previous_semester_code(20261) == 20252


def test_previous_and_next_are_inverse() -> None:
    for code in (20251, 20252, 20261, 20262):
        assert get_previous_semester_code(get_next_semester_code(code)) == code


def test_previous_semester_rejects_unknown_codes() -> None:
    with pytest.raises(ValueError):
        get_previous_semester_code(20253)
