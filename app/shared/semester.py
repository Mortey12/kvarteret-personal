from __future__ import annotations

from datetime import UTC, datetime


def format_semester_code(value: int | None) -> str | None:
    if value is None:
        return None
    year = value // 10
    season = value % 10
    if season == 1:
        return f"{year} Vår"
    if season == 2:
        return f"{year} Høst"
    return str(value)


def get_current_semester_code(value: datetime | None = None) -> int:
    moment = value.astimezone(UTC) if value is not None else datetime.now(UTC)
    season = 1 if moment.month <= 6 else 2
    return moment.year * 10 + season


def get_next_semester_code(value: int) -> int:
    year = value // 10
    season = value % 10
    if season == 1:
        return year * 10 + 2
    if season == 2:
        return (year + 1) * 10 + 1
    raise ValueError(f"Unsupported semester code: {value}")


def get_previous_semester_code(value: int) -> int:
    year = value // 10
    season = value % 10
    if season == 2:
        return year * 10 + 1
    if season == 1:
        return (year - 1) * 10 + 2
    raise ValueError(f"Unsupported semester code: {value}")
