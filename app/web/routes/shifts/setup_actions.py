"""Form posts for closed periods, templates, adding groups and the generator.

Each action redirects back with ``?notice=`` or ``?error=``; a group the leader may
not manage is a 403 with the reason in ``detail``.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status

from app.dependencies import get_shift_scope, get_shifts_setup_service, require_management_user
from app.domain.shifts.errors import (
    ShiftForbiddenError,
    ShiftNotFoundError,
    ShiftPlacementError,
)
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.setup_service import ShiftsSetupService
from app.observability import log_admin_activity
from app.web.route_helpers import redirect_to

router = APIRouter()


def _back(path: str, **params: str | int | None) -> str:
    query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
    return f"{path}?{query}" if query else path


async def _run(call, back, **back_params):
    """Await the service call. Returns (message, None) or (None, redirect on a rule error)."""
    try:
        return await call, None
    except ShiftForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ShiftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ShiftPlacementError as exc:
        return None, redirect_to(_back(back, error=str(exc), **back_params))


@router.post("/shifts/settings/{group_id}/enable")
async def shift_group_enable(
    request: Request,
    group_id: int,
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    setup: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    message, failed = await _run(setup.add_group(group_id, scope=scope), "/shifts/settings")
    if failed:
        return failed
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.group_enabled",
        subject_type="group",
        subject_id=group_id,
    )
    return redirect_to(_back("/shifts/settings", notice=message))


@router.post("/shifts/settings/{group_id}/closed-periods")
async def shift_closed_period_add(
    request: Request,
    group_id: int,
    starts_on: date = Form(...),
    ends_on: date = Form(...),
    label: str = Form(""),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    setup: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    message, failed = await _run(
        setup.add_closed_period(
            group_id, starts_on=starts_on, ends_on=ends_on, label=label, scope=scope
        ),
        "/shifts/settings",
    )
    if failed:
        return failed
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.closed_period_added",
        subject_type="group",
        subject_id=group_id,
        details={"starts_on": starts_on.isoformat(), "ends_on": ends_on.isoformat(), "label": label},
    )
    return redirect_to(_back("/shifts/settings", notice=message))


@router.post("/shifts/closed-periods/{period_id}/delete")
async def shift_closed_period_delete(
    request: Request,
    period_id: int,
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    setup: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    message, failed = await _run(setup.remove_closed_period(period_id, scope=scope), "/shifts/settings")
    if failed:
        return failed
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.closed_period_removed",
        subject_type="shift_closed_period",
        subject_id=period_id,
    )
    return redirect_to(_back("/shifts/settings", notice=message))


async def _form_fields(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: value for key, value in form.items() if isinstance(value, str)}


@router.post("/shifts/templates")
async def shift_template_create(
    request: Request,
    group_id: int = Form(...),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    setup: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    fields = await _form_fields(request)
    message, failed = await _run(
        setup.create_template(group_id, fields, scope=scope), "/shifts/templates"
    )
    if failed:
        return failed
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.template_created",
        subject_type="group",
        subject_id=group_id,
        details={"title": fields.get("title", "")},
    )
    return redirect_to(_back("/shifts/templates", notice=message))


@router.post("/shifts/templates/{template_id}")
async def shift_template_update(
    request: Request,
    template_id: int,
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    setup: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    fields = await _form_fields(request)
    message, failed = await _run(
        setup.update_template(template_id, fields, scope=scope), "/shifts/templates"
    )
    if failed:
        return failed
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.template_updated",
        subject_type="shift_template",
        subject_id=template_id,
    )
    return redirect_to(_back("/shifts/templates", notice=message))


@router.post("/shifts/templates/{template_id}/active")
async def shift_template_set_active(
    request: Request,
    template_id: int,
    active: bool = Form(...),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    setup: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    message, failed = await _run(
        setup.set_template_active(template_id, active, scope=scope), "/shifts/templates"
    )
    if failed:
        return failed
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.template_activated" if active else "shift.template_deactivated",
        subject_type="shift_template",
        subject_id=template_id,
    )
    return redirect_to(_back("/shifts/templates", notice=message))


@router.post("/shifts/generate")
async def shift_generate(
    request: Request,
    group_id: int = Form(...),
    date_from: date = Form(...),
    date_to: date = Form(...),
    current_user=Depends(require_management_user),
    scope: GroupScope = Depends(get_shift_scope),
    setup: ShiftsSetupService = Depends(get_shifts_setup_service),
):
    back = {"group": group_id}
    result, failed = await _run(
        setup.generate(group_id, date_from, date_to, scope=scope), "/shifts/generate", **back
    )
    if failed:
        return failed
    preview, message = result
    log_admin_activity(
        request=request,
        user=current_user,
        action="shift.generated",
        subject_type="group",
        subject_id=group_id,
        details={
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "created": len(preview.to_create),
            "existing": len(preview.existing),
            "closed": len(preview.closed),
        },
    )
    return redirect_to(_back("/shifts/generate", notice=message, **back))
