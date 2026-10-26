from __future__ import annotations


class ShiftNotFoundError(LookupError):
    """The place or assignment no longer exists (someone else changed the plan)."""


class ShiftPlacementError(ValueError):
    """A placement that breaks a rule. The message is shown to the leader."""
