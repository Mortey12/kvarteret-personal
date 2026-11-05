"""The qualifications a leader can put someone in training for, kept as rows in Personal.

A type has a ``key`` (stored on ``volunteer_qualifications``, never changed), a ``label`` the
leaders read, and a ``style``: "normal", or "skiftleder" (a skiftleder in training needs an
experienced skiftleder on the shift). It can be limited to one group and the groups below it, and
can be switched off. A named editor adds and changes types on the page ``/shifts/qualifications``;
no code or migration is needed for a new one.

A type only makes a person trainable and approvable, and links to a course (``courses.
training_qualification``). It does not change who may take which place: place kinds are separate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STYLES = ("normal", "skiftleder")
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,29}$")


@dataclass(frozen=True, slots=True)
class QualificationType:
    type_id: int
    key: str
    label: str
    style: str
    active: bool
    group_id: int | None
    sort_order: int


def offered_in(types: list[QualificationType], group_chain: list[int]) -> list[QualificationType]:
    """The active types a leader of this bar can use: for every bar, or for this group or a group
    above it (``group_chain`` is the bar and its ancestors)."""
    return [t for t in types if t.active and (t.group_id is None or t.group_id in group_chain)]


def ancestors(group_id: int, parents: dict[int, int | None]) -> list[int]:
    """The group and every group above it."""
    chain: list[int] = []
    current: int | None = group_id
    while current is not None and current not in chain:
        chain.append(current)
        current = parents.get(current)
    return chain
