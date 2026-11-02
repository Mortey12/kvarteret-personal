"""Skiftlista for volunteers in the app (kvarteret-internbevis).

Leaders plan in Personal (``ShiftsService``); volunteers use these calls
from the app: what is switched on, open places, their own shifts, taking
a place, swap and "kan ikke i dag", wishes and course sign-up.

Taking a place goes through the same checks as the leader board
(``ShiftsService._check``) plus what only applies in the app: age,
brann-/barkurs, and only the places the person may see.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.domain.shifts.app_repository import (
    ShiftsAppRepository,
)
from app.domain.shifts.app_rules import (
    utc_now,
)
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.app_claim import ClaimMixin
from app.domain.shifts.app_common import ShiftsAppCommon
from app.domain.shifts.app_courses import CoursesMixin
from app.domain.shifts.app_mine import MineMixin
from app.domain.shifts.app_wishes import WishesMixin
from app.domain.shifts.app_common import COURSE_HORIZON_DAYS, MAX_OPEN_DAYS  # noqa: F401  (re-exported)


class ShiftsAppService(ClaimMixin, MineMixin, WishesMixin, CoursesMixin, ShiftsAppCommon):
    def __init__(
        self,
        repository: ShiftsAppRepository,
        shifts_service: ShiftsService | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.shifts = shifts_service or ShiftsService(repository, now)
        self._now = now
