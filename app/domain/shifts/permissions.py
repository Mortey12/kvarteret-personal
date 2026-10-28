"""Which groups a leader may plan shifts for (pure rules, no I/O)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.auth.roles import UserRole
from app.domain.shifts.errors import ShiftForbiddenError


@dataclass(frozen=True, slots=True)
class GroupScope:
    """``group_ids`` is None for every group (Admin), else the permitted ids."""

    group_ids: frozenset[int] | None

    @property
    def is_all(self) -> bool:
        return self.group_ids is None

    @property
    def is_empty(self) -> bool:
        return self.group_ids is not None and not self.group_ids

    def allows(self, group_id: int) -> bool:
        return self.group_ids is None or group_id in self.group_ids

    def narrowed_to(self, group_ids: frozenset[int]) -> GroupScope:
        """Only the permitted groups that are also in ``group_ids`` (Admin keeps everything)."""
        if self.group_ids is None:
            return self
        return GroupScope(self.group_ids & group_ids)

    def require(self, group_id: int, group_name: str) -> None:
        if not self.allows(group_id):
            raise ShiftForbiddenError(
                f"Du har ikke tilgang til å endre skift for {group_name}."
            )


ALL_GROUPS = GroupScope(None)
NO_GROUPS = GroupScope(frozenset())


def with_subgroups(
    group_ids: Iterable[int], parent_by_group: Mapping[int, int | None]
) -> frozenset[int]:
    """The groups themselves plus every group below them (any depth)."""
    result = set(group_ids)
    grew = True
    while grew:
        grew = False
        for child, parent in parent_by_group.items():
            if parent in result and child not in result:
                result.add(child)
                grew = True
    return frozenset(result)


def participating_groups(
    direct_group_ids: Iterable[int], parent_by_group: Mapping[int, int | None]
) -> frozenset[int]:
    """Groups in Skiftlista: those with settings or templates, and their ancestors."""
    result: set[int] = set()
    for group_id in direct_group_ids:
        current: int | None = group_id
        while current is not None and current not in result and current in parent_by_group:
            result.add(current)
            current = parent_by_group[current]
    return frozenset(result)


def pool_group_ids(
    scope: GroupScope,
    participating: frozenset[int],
    parent_by_group: Mapping[int, int | None],
) -> frozenset[int]:
    """Whose members a leader can place: own groups plus their participating siblings."""
    if scope.group_ids is None:
        return participating
    parents = {parent_by_group.get(group_id) for group_id in scope.group_ids} - {None}
    siblings = {
        group_id for group_id in participating if parent_by_group.get(group_id) in parents
    }
    return frozenset(scope.group_ids | siblings)


def manageable_groups(
    role: UserRole,
    admin_group_ids: Iterable[int],
    parent_by_group: Mapping[int, int | None],
) -> GroupScope:
    """Admin: all groups. Gruppeadmin: assigned groups and their subgroups."""
    if role == UserRole.ADMIN:
        return ALL_GROUPS
    if role == UserRole.GROUP_ADMIN:
        return GroupScope(with_subgroups(admin_group_ids, parent_by_group))
    return NO_GROUPS
