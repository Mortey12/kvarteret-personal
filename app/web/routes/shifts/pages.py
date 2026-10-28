from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Request

from app.dependencies import (
    get_shift_scope,
    get_shifts_service,
    get_shifts_setup_service,
    require_management_user,
)
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.planning import OSLO
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.setup_service import ShiftsSetupService
from app.domain.shifts.errors import ShiftPlacementError
from app.domain.shifts.planning import SLOT_KINDS, WEEKDAYS_NO
from app.shared.semester import get_current_semester_code, get_semester_end_date
from app.errors import NotConfiguredError
from app.web.route_helpers import not_configured_http_exception
from app.web.templates import templates

router = APIRouter()


def _parse_week(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(OSLO).date()


def _parse_group(value: str | None) -> int | None:
    if value and value.isdigit():
        return int(value)
    return None


@router.get("/shifts")
async def shifts_week(
    request: Request,
    week: str | None = None,
    group: str | None = None,
    notice: str | None = None,
    error: str | None = None,
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
):
    try:
        plan = await shifts_service.get_week_plan(
            _parse_week(week), _parse_group(group), scope=scope
        )
    except NotConfiguredError:
        raise not_configured_http_exception("Skiftplanen er ikke satt opp ennå.")
    return templates.TemplateResponse(
        request,
        "pages/shifts/shift_plan.html",
        {
            "title": "Skiftplan",
            "section": "shifts",
            "current_user": current_user,
            "plan": plan,
            "no_groups": scope.is_empty,
            "notice": notice,
            "error": error,
        },
    )


@router.get("/shifts/settings")
async def shift_settings(
    request: Request,
    notice: str | None = None,
    error: str | None = None,
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    shifts_service: ShiftsService = Depends(get_shifts_service),
    setup_service: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    settings = [] if scope.is_empty else await shifts_service.list_group_settings(scope)
    closed_periods = await setup_service.list_closed_periods(scope)
    other_groups = await setup_service.list_other_groups(scope)
    return templates.TemplateResponse(
        request,
        "pages/shifts/shift_settings.html",
        {
            "title": "Skiftlista: innstillinger",
            "section": "shifts",
            "current_user": current_user,
            "group_settings": settings,
            "closed_periods": closed_periods,
            "other_groups": other_groups,
            "no_groups": scope.is_empty,
            "notice": notice,
            "error": error,
        },
    )


@router.get("/shifts/templates")
async def shift_templates_page(
    request: Request,
    notice: str | None = None,
    error: str | None = None,
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    setup_service: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    groups = [] if scope.is_empty else await setup_service.permitted_groups(scope)
    by_group = {group.group_id: [] for group in groups}
    for template in await setup_service.list_templates(scope):
        by_group[template.group_id].append(template)
    return templates.TemplateResponse(
        request,
        "pages/shifts/shift_templates.html",
        {
            "title": "Skiftlista: maler",
            "section": "shifts",
            "current_user": current_user,
            "groups": groups,
            "templates_by_group": by_group,
            "slot_kinds": SLOT_KINDS,
            "weekdays": WEEKDAYS_NO,
            "slot_rows": 8,
            "no_groups": scope.is_empty,
            "notice": notice,
            "error": error,
        },
    )


def _parse_date(value: str | None, default: date) -> date:
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


@router.get("/shifts/generate")
async def shift_generate_page(
    request: Request,
    group: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    notice: str | None = None,
    error: str | None = None,
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    setup_service: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    groups = [] if scope.is_empty else await setup_service.permitted_groups(scope)
    today = datetime.now(OSLO).date()
    start = _parse_date(date_from, today)
    end = _parse_date(date_to, get_semester_end_date(get_current_semester_code()))
    group_id = _parse_group(group)
    preview = None
    if group_id is not None and any(option.group_id == group_id for option in groups):
        try:
            preview = await setup_service.preview_generation(group_id, start, end, scope=scope)
        except ShiftPlacementError as exc:
            error = error or str(exc)
    return templates.TemplateResponse(
        request,
        "pages/shifts/shift_generate.html",
        {
            "title": "Skiftlista: lag skift",
            "section": "shifts",
            "current_user": current_user,
            "groups": groups,
            "selected_group_id": group_id,
            "date_from": start,
            "date_to": end,
            "preview": preview,
            "no_groups": scope.is_empty,
            "notice": notice,
            "error": error,
        },
    )
