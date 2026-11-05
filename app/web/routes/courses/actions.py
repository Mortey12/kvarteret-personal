from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.dependencies import get_courses_service, get_settings, get_shifts_service, require_management_user
from app.domain.courses.service import (
    CourseCompletionNotFoundError,
    CourseDeleteBlockedError,
    CoursesService,
    DuplicateCourseCompletionError,
    InvalidCourseCompletionError,
)
from app.domain.courses.training_courses import LOCKED_TEXT, may_edit_training_course
from app.web.route_helpers import blocked_http_exception, log_and_redirect

router = APIRouter()


async def _require_course_editable(course_id: int, user, courses_service: CoursesService, settings) -> None:
    """Skjenkekurs, Kokkekurs and Skiftlederkurs are not edited by every admin: only the few named
    in TRAINING_COURSE_EDITORS can, because the courses are registered by approving a training."""
    if await courses_service.get_training_qualification(course_id) is None:
        return  # not marked: an ordinary course, nothing changes for it
    if not may_edit_training_course(getattr(user, "email", None), settings.training_course_editors):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LOCKED_TEXT)


@router.post("/courses")
async def courses_create(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(default=None),
    current_user=Depends(require_management_user),
    courses_service: CoursesService = Depends(get_courses_service),
    settings=Depends(get_settings),
):
    course_id = await courses_service.create_course(name=name, description=description)
    return log_and_redirect(
        request=request,
        user=current_user,
        action="course.create",
        subject_type="course",
        subject_id=course_id,
        redirect_path=f"/courses/{course_id}",
    )


@router.patch("/courses/{course_id}")
async def courses_update(
    request: Request,
    course_id: int,
    name: str = Form(...),
    description: str | None = Form(default=None),
    current_user=Depends(require_management_user),
    courses_service: CoursesService = Depends(get_courses_service),
    settings=Depends(get_settings),
):
    await _require_course_editable(course_id, current_user, courses_service, settings)
    updated = await courses_service.update_course(
        course_id, name=name, description=description
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found."
        )
    return log_and_redirect(
        request=request,
        user=current_user,
        action="course.update",
        subject_type="course",
        subject_id=course_id,
        redirect_path=f"/courses/{course_id}",
    )


@router.delete("/courses/{course_id}")
async def courses_delete(
    request: Request,
    course_id: int,
    current_user=Depends(require_management_user),
    courses_service: CoursesService = Depends(get_courses_service),
    settings=Depends(get_settings),
):
    await _require_course_editable(course_id, current_user, courses_service, settings)
    try:
        deleted = await courses_service.delete_course(course_id)
    except CourseDeleteBlockedError as exc:
        raise blocked_http_exception(exc.blockers) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found."
        )
    return log_and_redirect(
        request=request,
        user=current_user,
        action="course.delete",
        subject_type="course",
        subject_id=course_id,
        redirect_path="/courses",
    )


@router.post("/courses/{course_id}/training-settings")
async def courses_training_settings(
    request: Request,
    course_id: int,
    enabled: str | None = Form(default=None),
    qualification: str = Form(default=""),
    current_user=Depends(require_management_user),
    courses_service: CoursesService = Depends(get_courses_service),
    settings=Depends(get_settings),
    shifts_service=Depends(get_shifts_service),
):
    """On or off: is this course registered by approving a finished training in Skiftlista, and for
    which qualification. Only the named editors can change it; for every other course it stays off."""
    if not may_edit_training_course(getattr(current_user, "email", None), settings.training_course_editors):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=LOCKED_TEXT)
    if await courses_service.get_course_detail(course_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found.")
    chosen = qualification.strip() if enabled else None
    if enabled and not chosen:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Velg hvilken opplæring kurset hører til.")
    try:
        allowed = {t.key for t in await shifts_service.repository.list_qualification_types() if t.active}
        await courses_service.set_training_qualification(course_id, chosen, allowed=allowed)
    except InvalidCourseCompletionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return log_and_redirect(
        request=request,
        user=current_user,
        action="course.training_settings",
        subject_type="course",
        subject_id=course_id,
        details={"training_qualification": chosen},
        redirect_path=f"/courses/{course_id}",
    )


@router.post("/courses/{course_id}/completions")
async def courses_add_completion(
    request: Request,
    course_id: int,
    volunteer_ids: list[int] = Form(default=[]),
    year: int = Form(...),
    term: int = Form(...),
    current_user=Depends(require_management_user),
    courses_service: CoursesService = Depends(get_courses_service),
    settings=Depends(get_settings),
):
    await _require_course_editable(course_id, current_user, courses_service, settings)
    try:
        created_count = await courses_service.create_course_completions(
            course_id=course_id,
            volunteer_ids=volunteer_ids,
            year=year,
            term=term,
        )
    except HTTPException:
        raise
    except (DuplicateCourseCompletionError, InvalidCourseCompletionError) as exc:
        return RedirectResponse(
            url=f"/courses/{course_id}?completion_error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return log_and_redirect(
        request=request,
        user=current_user,
        action="course_completion.create",
        subject_type="course",
        subject_id=course_id,
        details={
            "volunteer_ids": volunteer_ids,
            "created_count": created_count,
            "year": year,
            "term": term,
        },
        redirect_path=f"/courses/{course_id}",
    )


@router.delete("/courses/{course_id}/completions/{completion_id}")
async def courses_delete_completion(
    request: Request,
    course_id: int,
    completion_id: int,
    current_user=Depends(require_management_user),
    courses_service: CoursesService = Depends(get_courses_service),
    settings=Depends(get_settings),
):
    await _require_course_editable(course_id, current_user, courses_service, settings)
    try:
        await courses_service.delete_course_completion(
            course_id=course_id, completion_id=completion_id
        )
    except CourseCompletionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return log_and_redirect(
        request=request,
        user=current_user,
        action="course_completion.delete",
        subject_type="course_completion",
        subject_id=completion_id,
        details={"course_id": course_id},
        redirect_path=f"/courses/{course_id}",
    )
