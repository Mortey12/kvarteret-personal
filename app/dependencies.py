from __future__ import annotations

from app.observability import get_logger

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from app.auth.cookies import SessionCookieSigner
from app.auth.demo_guard import DemoDatabaseGuard
from app.auth.login_service import LoginService
from app.auth.password_reset_service import PasswordResetService
from app.auth.repository import DatabaseAuthRepository
from app.db.rate_limit import RateLimiter
from app.auth.models import AuthenticatedUser, WebSession
from app.auth.roles import UserRole
from app.media_tokens import MediaTokenService
from app.runtime import ApplicationContainer
from app.domain.agent_chat.service import AgentChatService
from app.domain.event_interest.repository import EventInterestRepository
from app.domain.event_interest.service import EventInterestService
from app.domain.feedback.service import FeedbackService
from app.domain.courses.service import CoursesService
from app.domain.shifts.app_service import ShiftsAppService
from app.domain.shifts.permissions import GroupScope
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.setup_service import ShiftsSetupService
from app.domain.groups.service import GroupsService
from app.domain.mobile_card.service import MobileCardService
from app.domain.mobile_card.april_state import MobileCardAprilStateService
from app.domain.spotify.now_playing import NowPlayingService
from app.domain.role_assignments.service import RoleAssignmentsService
from app.domain.volunteers.service import VolunteersService
from app.domain.volunteer_applications.service import VolunteerApplicationsService
from app.domain.search import VolunteerSearchService
from app.domain.role_assignments.semester_transfer import SemesterTransferService
from app.domain.admin_accounts.service import AdminAccountsService

logger = get_logger(__name__)


@dataclass(slots=True)
class RequestAuthContext:
    session: WebSession | None
    user: AuthenticatedUser | None


def get_container(request: Request) -> ApplicationContainer:
    return request.app.state.container


def get_settings(request: Request):
    return get_container(request).settings


def get_session_store(request: Request):
    return get_container(request).session_store


def get_session_cookie_signer(request: Request) -> SessionCookieSigner:
    return get_container(request).session_cookie_signer


def get_media_token_service(request: Request) -> MediaTokenService:
    return get_container(request).media_token_service


def get_request_auth_context(request: Request) -> RequestAuthContext:
    return RequestAuthContext(
        session=getattr(request.state, "session", None),
        user=getattr(request.state, "current_user", None),
    )


def get_current_user(request: Request) -> AuthenticatedUser | None:
    return getattr(request.state, "current_user", None)


def require_authenticated_user(
    current_user: AuthenticatedUser | None = Depends(get_current_user),
) -> AuthenticatedUser:
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    return current_user


def require_admin_user(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    return _require_admin_user(current_user, detail="Admin access is required.")


def require_management_user(
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    if current_user.role not in {UserRole.ADMIN, UserRole.GROUP_ADMIN}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access is required."
        )
    return current_user


def _require_admin_user(
    current_user: AuthenticatedUser, *, detail
) -> AuthenticatedUser:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return current_user


def get_login_service(request: Request) -> LoginService:
    return get_container(request).login_service


def get_password_reset_service(request: Request) -> PasswordResetService:
    return get_container(request).password_reset_service


def get_auth_repository(request: Request) -> DatabaseAuthRepository:
    return get_container(request).auth_repository


def get_demo_guard(request: Request) -> DemoDatabaseGuard:
    return get_container(request).demo_guard


def get_rate_limiter(request: Request) -> RateLimiter:
    return get_container(request).rate_limiter


def get_supabase_auth_gateway(request: Request):
    return get_container(request).supabase_auth_gateway


def get_email_sender(request: Request):
    return get_container(request).email_sender


def get_email_outbox_service(request: Request):
    return get_container(request).email_outbox_service


def get_volunteers_service(request: Request) -> VolunteersService:
    return get_container(request).volunteers_service


def get_role_assignments_service(request: Request) -> RoleAssignmentsService:
    return get_container(request).role_assignments_service


def get_groups_service(request: Request) -> GroupsService:
    return get_container(request).groups_service


def get_courses_service(request: Request) -> CoursesService:
    return get_container(request).courses_service


def get_shifts_service(request: Request) -> ShiftsService:
    return get_container(request).shifts_service


def get_shifts_setup_service(request: Request) -> ShiftsSetupService:
    return get_container(request).shifts_setup_service


async def get_shift_scope(
    current_user: AuthenticatedUser = Depends(require_management_user),
    shifts_service: ShiftsService = Depends(get_shifts_service),
) -> GroupScope:
    """The groups the signed-in leader may plan shifts for."""
    return await shifts_service.manageable_scope(current_user.role, current_user.auth_user_id)


def get_shifts_app_service(request: Request) -> ShiftsAppService:
    return get_container(request).shifts_app_service


def get_volunteer_search_service(request: Request) -> VolunteerSearchService:
    return get_container(request).volunteer_search_service


def get_admin_accounts_service(request: Request) -> AdminAccountsService:
    return get_container(request).admin_accounts_service


def get_mobile_card_service(request: Request) -> MobileCardService:
    return get_container(request).mobile_card_service


def get_mobile_card_april_state_service(
    request: Request,
) -> MobileCardAprilStateService:
    return get_container(request).mobile_card_april_state_service


def get_now_playing_service(request: Request) -> NowPlayingService:
    return get_container(request).now_playing_service


def get_volunteer_applications_service(
    request: Request,
) -> VolunteerApplicationsService:
    return get_container(request).volunteer_applications_service


def get_semester_transfer_service(request: Request) -> SemesterTransferService:
    return get_container(request).semester_transfer_service


def get_agent_chat_service(request: Request) -> AgentChatService:
    return get_container(request).agent_chat_service


def get_feedback_service(request: Request) -> FeedbackService:
    return get_container(request).feedback_service


async def load_web_navigation_state(
    request: Request,
    current_user: AuthenticatedUser | None = Depends(get_current_user),
    volunteer_applications_service: VolunteerApplicationsService = Depends(
        get_volunteer_applications_service
    ),
) -> None:
    request.state.show_admin_tools = False
    request.state.show_management_tools = False
    request.state.volunteer_application_pending_count = 0
    if current_user is None or _is_fragment_request(request):
        return
    if current_user.role not in {UserRole.ADMIN, UserRole.GROUP_ADMIN}:
        return
    request.state.show_management_tools = True
    request.state.show_admin_tools = current_user.role == UserRole.ADMIN
    try:
        request.state.volunteer_application_pending_count = (
            await volunteer_applications_service.count_pending_volunteer_applications()
        )
    except Exception:
        logger.exception("volunteer.pending_count.failed", extra={"event": "volunteer.pending_count.failed"})
        request.state.volunteer_application_pending_count = 0


def _is_fragment_request(request: Request) -> bool:
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Boosted") != "true"
    )


def get_event_interest_service() -> EventInterestService:
    return EventInterestService(EventInterestRepository())
