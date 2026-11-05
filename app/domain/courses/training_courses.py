"""Courses that are registered by approving a finished training, and who may change them by hand.

A course with ``courses.training_qualification`` set (skjenker, kokk or skiftleder) is registered
when a leader approves a finished training in Skiftlista (``domain/shifts/training_service.py``).
Such a course is not like the others: nobody should add or remove who has completed it from the
courses page just by being an admin. Only the few people named in the ``TRAINING_COURSE_EDITORS``
setting (e-mail addresses, comma separated) can, and only they can say which course it is. With
the setting empty, nobody can. Other courses (fire safety and so on) are not touched.

For another group's course later: mark the course with its qualification on the course page. The
qualifications are rows of ``qualification_types`` that a named editor adds on ``/shifts/qualifications``;
no code change is needed.
"""

from __future__ import annotations

LOCKED_TEXT = (
    "Dette kurset registreres når en leder godkjenner opplæring i Skiftlista. "
    "Bare noen få personer kan endre det her."
)


def may_edit_training_course(email: str | None, editors: str) -> bool:
    """Whether this e-mail address is one of the named editors."""
    allowed = {item.strip().lower() for item in editors.split(",") if item.strip()}
    return bool(email) and email.strip().lower() in allowed
