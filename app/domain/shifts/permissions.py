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


def ancestor_chain(group_id: int, parent_by_group: Mapping[int, int | None]) -> list[int]:
    """The group itself, then its parent and so on up to the root."""
    chain: list[int] = []
    current: int | None = group_id
    while current is not None and current not in chain and current in parent_by_group:
        chain.append(current)
        current = parent_by_group[current]
    return chain


def participating_groups(
    direct_group_ids: Iterable[int], parent_by_group: Mapping[int, int | None]
) -> frozenset[int]:
    """Groups in Skiftlista: those with settings or templates, and their ancestors."""
    result: set[int] = set()
    for group_id in direct_group_ids:
        result.update(ancestor_chain(group_id, parent_by_group))
    return frozenset(result)


def tree_order(
    parent_by_group: Mapping[int, int | None], names: Mapping[int, str]
) -> list[int]:
    """Every group, each followed by its subgroups; siblings by name."""
    children: dict[int | None, list[int]] = {}
    for group_id, parent in parent_by_group.items():
        key = parent if parent in parent_by_group else None
        children.setdefault(key, []).append(group_id)
    ordered: list[int] = []

    def visit(group_id: int) -> None:
        ordered.append(group_id)
        for child in sorted(children.get(group_id, []), key=lambda g: names.get(g, "").casefold()):
            visit(child)

    for root in sorted(children.get(None, []), key=lambda g: names.get(g, "").casefold()):
        visit(root)
    return ordered


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
