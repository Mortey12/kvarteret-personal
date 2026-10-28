"""Setting up Skiftlista for a group: taking part, closed periods, templates and the
semester generator. Every call is scoped to the groups the leader may manage."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta

from app.domain.shifts.errors import (
    ShiftForbiddenError,
    ShiftNotFoundError,
    ShiftPlacementError,
)
from app.domain.shifts.generator import MAX_RANGE_DAYS, plan_generation
from app.domain.shifts.models import (
    ClosedPeriod,
    GenerationPreview,
    OtherGroup,
    ShiftGroupOption,
    ShiftTemplate,
)
from app.domain.shifts.permissions import GroupScope, participating_groups
from app.domain.shifts.planning import OSLO, format_day
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.template_input import parse_template_form

LABEL_MAX = 100
DEFAULT_PERIOD_LABEL = "Stengt"


class ShiftsSetupService:
    def __init__(self, repository: ShiftsRepository) -> None:
        self.repository = repository

    # ---------------------------------------------------------------- groups

    async def _participating(self) -> tuple[frozenset[int], dict[int, str]]:
        parents = await self.repository.list_group_parents()
        direct = await self.repository.list_direct_participating_group_ids()
        names = {
            row["id"]: row["name"] for row in await self.repository.list_groups_with_settings()
        }
        return participating_groups(direct, parents), names

    async def permitted_groups(self, scope: GroupScope) -> list[ShiftGroupOption]:
        """Participating groups the leader may manage, by name."""
        participating, names = await self._participating()
        return sorted(
            (
                ShiftGroupOption(group_id, names[group_id])
                for group_id in participating
                if group_id in names and scope.allows(group_id)
            ),
            key=lambda option: option.name.casefold(),
        )

    async def _require_group(self, group_id: int, scope: GroupScope) -> str:
        participating, names = await self._participating()
        name = names.get(group_id)
        if name is None:
            raise ShiftNotFoundError("Gruppen finnes ikke.")
        scope.require(group_id, name)
        if group_id not in participating:
            raise ShiftForbiddenError(f"{name} tar ikke del i Skiftlista ennå.")
        return name

    async def list_other_groups(self, scope: GroupScope) -> list[OtherGroup]:
        """Groups an Admin can still add to Skiftlista (empty for everyone else)."""
        if not scope.is_all:
            return []
        participating, names = await self._participating()
        return [
            OtherGroup(group_id, name)
            for group_id, name in sorted(names.items(), key=lambda item: item[1].casefold())
            if group_id not in participating
        ]

    async def add_group(self, group_id: int, *, scope: GroupScope) -> str:
        if not scope.is_all:
            raise ShiftForbiddenError(
                "Bare en administrator kan slå på Skiftlista for en ny gruppe."
            )
        participating, names = await self._participating()
        name = names.get(group_id)
        if name is None:
            raise ShiftNotFoundError("Gruppen finnes ikke.")
        if group_id in participating:
            raise ShiftPlacementError(f"{name} tar allerede del i Skiftlista.")
        await self.repository.save_group_settings(
            group_id, app_enabled=False, samf_enabled=False, meeting_point=None
        )
        return f"{name} tar nå del i Skiftlista. Begge bryterne er av til du slår dem på."

    # --------------------------------------------------------- closed periods

    async def list_closed_periods(self, scope: GroupScope) -> dict[int, list[ClosedPeriod]]:
        ids = [option.group_id for option in await self.permitted_groups(scope)]
        result: dict[int, list[ClosedPeriod]] = {group_id: [] for group_id in ids}
        for period in await self.repository.list_closed_periods(ids):
            result[period.group_id].append(period)
        return result

    async def add_closed_period(
        self, group_id: int, *, starts_on: date, ends_on: date, label: str, scope: GroupScope
    ) -> str:
        name = await self._require_group(group_id, scope)
        if ends_on < starts_on:
            raise ShiftPlacementError("Sluttdatoen kan ikke være før startdatoen.")
        text = label.strip() or DEFAULT_PERIOD_LABEL
        if len(text) > LABEL_MAX:
            raise ShiftPlacementError(f"Navnet kan ha opptil {LABEL_MAX} tegn.")
        await self.repository.add_closed_period(group_id, starts_on, ends_on, text)
        return f"{text} er lagt til for {name}: ingen skift fra {format_day(starts_on).lower()} til {format_day(ends_on).lower()}."

    async def remove_closed_period(self, period_id: int, *, scope: GroupScope) -> str:
        period = await self.repository.get_closed_period(period_id)
        if period is None:
            raise ShiftNotFoundError("Perioden finnes ikke lenger.")
        name = await self._require_group(period.group_id, scope)
        await self.repository.delete_closed_period(period_id)
        return f"{period.label} er fjernet for {name}."

    # -------------------------------------------------------------- templates

    async def list_templates(self, scope: GroupScope) -> list[ShiftTemplate]:
        ids = [option.group_id for option in await self.permitted_groups(scope)]
        return await self.repository.list_templates(ids)

    async def _require_template(self, template_id: int, scope: GroupScope) -> ShiftTemplate:
        template = await self.repository.get_template(template_id)
        if template is None:
            raise ShiftNotFoundError("Malen finnes ikke lenger.")
        await self._require_group(template.group_id, scope)
        return template

    async def create_template(
        self, group_id: int, form: Mapping[str, str], *, scope: GroupScope
    ) -> str:
        name = await self._require_group(group_id, scope)
        data = parse_template_form(form)
        await self.repository.create_template(group_id, data)
        return f"Malen «{data.title}» er lagt til for {name}."

    async def update_template(
        self, template_id: int, form: Mapping[str, str], *, scope: GroupScope
    ) -> str:
        await self._require_template(template_id, scope)
        data = parse_template_form(form)
        await self.repository.update_template(template_id, data)
        return (
            f"Malen «{data.title}» er lagret. Skift som allerede er laget, endres ikke."
        )

    async def set_template_active(self, template_id: int, active: bool, *, scope: GroupScope) -> str:
        template = await self._require_template(template_id, scope)
        await self.repository.set_template_active(template_id, active)
        if active:
            return f"Malen «{template.title}» er aktiv igjen."
        return f"Malen «{template.title}» er slått av. Eksisterende skift beholdes."

    # -------------------------------------------------------------- generator

    async def preview_generation(
        self, group_id: int, date_from: date, date_to: date, *, scope: GroupScope
    ) -> GenerationPreview:
        name = await self._require_group(group_id, scope)
        if date_to < date_from:
            raise ShiftPlacementError("Sluttdatoen kan ikke være før startdatoen.")
        if (date_to - date_from).days > MAX_RANGE_DAYS:
            raise ShiftPlacementError(f"Velg en periode på høyst {MAX_RANGE_DAYS} dager.")
        templates = await self.repository.list_templates([group_id])
        periods = await self.repository.list_closed_periods([group_id])
        existing = await self.repository.list_existing_shift_starts(
            [t.template_id for t in templates],
            datetime.combine(date_from - timedelta(days=1), time.min, tzinfo=OSLO),
            datetime.combine(date_to + timedelta(days=2), time.min, tzinfo=OSLO),
        )
        return plan_generation(
            group_id=group_id,
            group_name=name,
            templates=templates,
            periods=periods,
            existing=existing,
            date_from=date_from,
            date_to=date_to,
        )

    async def generate(
        self, group_id: int, date_from: date, date_to: date, *, scope: GroupScope
    ) -> tuple[GenerationPreview, str]:
        """Create what the preview shows now. Existing shifts are never touched."""
        preview = await self.preview_generation(group_id, date_from, date_to, scope=scope)
        for planned in preview.to_create:
            await self.repository.create_planned_shift(planned)
        created = len(preview.to_create)
        message = (
            f"Opprettet {created} skift for {preview.group_name}. "
            f"{len(preview.existing)} fantes fra før, {len(preview.closed)} hoppet over fordi gruppen er stengt."
        )
        return preview, message
