from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.roles import UserRole
from app.dependencies import (
    get_courses_service,
    get_settings,
    require_authenticated_user,
    require_management_user,
)
from app.errors import NotConfiguredError
from app.shared.semester import get_current_semester_code
from app.domain.volunteers.options import SEMESTER_TERM_OPTIONS
from app.domain.courses.service import CoursesService
from app.domain.courses.training_courses import LOCKED_TEXT, is_training_course, may_edit_training_course
from app.web.route_helpers import not_configured_http_exception
from app.web.templates import templates

router = APIRouter()


@router.get("/courses")
async def courses_index(
    request: Request,
    q: str | None = None,
    current_user=Depends(require_authenticated_user),
    courses_service: CoursesService = Depends(get_courses_service),
):
    try:
        courses = await courses_service.list_courses(query=q, limit=100)
    except NotConfiguredError:
        raise not_configured_http_exception(
            "Database-backed course views are not configured yet."
        )
    return templates.TemplateResponse(
        request,
        "pages/courses/courses.html",
        {
            "title": "Courses",
            "section": "courses",
            "current_user": current_user,
            "can_manage_course": current_user.role
            in {UserRole.ADMIN, UserRole.GROUP_ADMIN},
            "courses": courses,
            "query": q or "",
        },
    )


@router.get("/courses/new")
async def courses_new(
    request: Request,
    current_user=Depends(require_management_user),
):
    return templates.TemplateResponse(
        request,
        "pages/courses/course_new.html",
        {
            "title": "Nytt kurs",
            "section": "courses",
            "current_user": current_user,
        },
    )


@router.get("/courses/{course_id}")
async def courses_detail(
    request: Request,
    course_id: int,
    current_user=Depends(require_authenticated_user),
    courses_service: CoursesService = Depends(get_courses_service),
    settings=Depends(get_settings),
):
    try:
        course = await courses_service.get_course_detail(course_id)
    except NotConfiguredError:
        raise not_configured_http_exception(
            "Database-backed course views are not configured yet."
        )
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Course not found."
        )
    can_manage = current_user.role in {UserRole.ADMIN, UserRole.GROUP_ADMIN}
    # Skjenkekurs, Kokkekurs and Skiftlederkurs are registered by approving a training: the forms
    # are hidden unless the user is one of the few named editors.
    locked = is_training_course(course.name) and not may_edit_training_course(
        getattr(current_user, "email", None), settings.training_course_editors
    )
    can_manage = can_manage and not locked
    return templates.TemplateResponse(
        request,
        "pages/courses/course_detail.html",
        {
            "title": course.name,
            "section": "courses",
            "current_user": current_user,
            "can_manage_course": can_manage,
            "training_course_locked": locked,
            "training_course_locked_text": LOCKED_TEXT,
            "course": course,
            "default_completion_year": get_current_semester_code() // 10,
            "default_completion_term": get_current_semester_code() % 10,
            "semester_term_options": SEMESTER_TERM_OPTIONS,
            "completion_error": request.query_params.get("completion_error"),
        },
    )
