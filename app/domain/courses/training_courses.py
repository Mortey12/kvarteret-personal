"""The three courses that belong to Skiftlista's training, and who may change them by hand.

Skjenkekurs, Kokkekurs and Skiftlederkurs are registered when a leader approves a finished
training (``domain/shifts/training_service.py``). They are not like other courses: nobody should
add or remove who has completed them from the courses page just by being an admin. Only the few
people named in the ``TRAINING_COURSE_EDITORS`` setting (e-mail addresses, comma separated) can;
with the setting empty, nobody can. Other courses (fire safety and so on) are not touched.
"""

from __future__ import annotations

TRAINING_COURSE_BY_QUALIFICATION = {
    "skjenker": "Skjenkekurs",
    "kokk": "Kokkekurs",
    "skiftleder": "Skiftlederkurs",
}
_NAMES = frozenset(name.lower() for name in TRAINING_COURSE_BY_QUALIFICATION.values())

LOCKED_TEXT = (
    "Dette kurset registreres når en leder godkjenner opplæring i Skiftlista. "
    "Bare noen få personer kan endre det her."
)


def is_training_course(name: str | None) -> bool:
    return bool(name) and name.strip().lower() in _NAMES


def may_edit_training_course(email: str | None, editors: str) -> bool:
    """Whether this e-mail address is one of the named editors."""
    allowed = {item.strip().lower() for item in editors.split(",") if item.strip()}
    return bool(email) and email.strip().lower() in allowed
