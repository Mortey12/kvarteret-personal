from __future__ import annotations

import pytest

from app.auth.roles import UserRole
from app.domain.shifts.errors import ShiftForbiddenError
from app.domain.shifts.permissions import manageable_groups, with_subgroups

# Skjenke (1) > Stjerne (2), Grøndahls (3) > Pub-kjeller (4); Annet (5) is separate.
PARENTS = {1: None, 2: 1, 3: 1, 4: 3, 5: None}


def test_admin_manages_every_group() -> None:
    scope = manageable_groups(UserRole.ADMIN, [], PARENTS)
    assert scope.group_ids is None
    assert scope.allows(5) and not scope.is_empty


def test_gruppeadmin_of_a_leaf_group_manages_only_that_group() -> None:
    scope = manageable_groups(UserRole.GROUP_ADMIN, [2], PARENTS)
    assert scope.allows(2) and not scope.allows(3) and not scope.allows(1)


def test_gruppeadmin_covers_subgroups_at_any_depth() -> None:
    scope = manageable_groups(UserRole.GROUP_ADMIN, [1], PARENTS)
    assert scope.group_ids == frozenset({1, 2, 3, 4})
    assert with_subgroups([3], PARENTS) == frozenset({3, 4})


def test_gruppeadmin_without_groups_and_other_roles_manage_nothing() -> None:
    assert manageable_groups(UserRole.GROUP_ADMIN, [], PARENTS).is_empty
    assert manageable_groups(UserRole.VOLUNTEER, [1], PARENTS).is_empty


def test_require_refuses_with_a_norwegian_message() -> None:
    scope = manageable_groups(UserRole.GROUP_ADMIN, [2], PARENTS)
    scope.require(2, "Stjerne")
    with pytest.raises(ShiftForbiddenError, match="Du har ikke tilgang til å endre skift for Grøndahls"):
        scope.require(3, "Grøndahls")
