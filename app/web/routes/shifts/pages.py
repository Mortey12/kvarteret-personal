from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Request

from app.dependencies import get_shift_scope, get_shifts_service, require_management_user
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.planning import OSLO
from app.domain.shifts.service import ShiftsService
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
):
    settings = [] if scope.is_empty else await shifts_service.list_group_settings(scope)
    return templates.TemplateResponse(
        request,
        "pages/shifts/shift_settings.html",
        {
            "title": "Skiftlista: innstillinger",
            "section": "shifts",
            "current_user": current_user,
            "group_settings": settings,
            "no_groups": scope.is_empty,
            "notice": notice,
            "error": error,
        },
    )
