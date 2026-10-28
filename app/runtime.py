from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.cookies import SessionCookieSigner
from app.auth.demo_guard import DemoDatabaseGuard
from app.auth.demo_login import demo_login_enabled
from app.auth.dev_auth import DevAuthGateway
from app.auth.login_service import LoginService
from app.auth.password_reset_service import PasswordResetService
from app.auth.repository import DatabaseAuthRepository
from app.auth.session_store import (
    SessionRepositoryProtocol,
    SessionStore,
    SessionStoreProtocol,
)
from app.auth.supabase_auth import SupabaseAuthGateway, SupabaseAuthGatewayProtocol
from app.config import Settings, get_settings, validate_production_secrets
from app.db.rate_limit import PostgresRateLimiter
from app.db.session import DatabaseRuntimeManager, session_scope
from app.errors import NotConfiguredError
from app.media_tokens import MediaTokenService
from app.infrastructure.email.admin_account_templates import (
    AdminAccountEmailTemplateRenderer,
)
from app.infrastructure.email.applicant_templates import ApplicantEmailTemplateRenderer
from app.infrastructure.email.mobile_card_templates import (
    MobileCardEmailTemplateRenderer,
)
from app.infrastructure.email.password_reset_templates import (
    PasswordResetEmailTemplateRenderer,
)
from app.infrastructure.email.console import ConsoleEmailSender
from app.infrastructure.media.photo_processing import process_uploaded_photo
from app.infrastructure.email.resend import ResendEmailSender
from app.infrastructure.email.protocols import EmailSenderProtocol
from app.domain.feedback.service import FeedbackService
from app.domain.mobile_card.repository import MobileCardRepository
from app.domain.mobile_card.april_state import (
    MobileCardAprilStateRepository,
    MobileCardAprilStateService,
)
from app.domain.spotify.repository import IntegrationTokensRepository
from app.domain.spotify.now_playing import NowPlayingService
from app.domain.volunteers.repository import VolunteersRepository
from app.domain.volunteer_applications.repository import VolunteerApplicationsRepository
from app.domain.courses.service import CoursesService
from app.domain.courses.repository import CoursesRepository
from app.domain.shifts.app_repository import ShiftsAppRepository
from app.domain.shifts.app_service import ShiftsAppService
from app.domain.shifts.repository import ShiftsRepository
from app.domain.shifts.service import ShiftsService
from app.domain.shifts.setup_service import ShiftsSetupService
from app.domain.agent_chat.repository import AgentChatRepository
from app.domain.agent_chat.service import AgentChatService
from app.domain.groups.service import GroupsService
from app.domain.mobile_card.service import MobileCardService
from app.domain.volunteers.service import VolunteersService
from app.domain.volunteer_applications.service import VolunteerApplicationsService
from app.domain.search import VolunteerSearchRepository, VolunteerSearchService
from app.domain.role_assignments.repository import RoleAssignmentsRepository
from app.domain.role_assignments.semester_transfer import SemesterTransferService
from app.domain.role_assignments.service import RoleAssignmentsService
from app.infrastructure.storage.local_dir import LocalDirectoryStorage
from app.infrastructure.storage.service import StorageService
from app.infrastructure.storage.protocols import StorageProtocol
from app.domain.admin_accounts.service import AdminAccountsService
from app.domain.admin_accounts.repository import AdminAccountsRepository
from app.email_outbox_service import EmailOutboxService


# Keep the app bootable in local and test environments that do not have live
# Supabase credentials, while still failing fast once a protected auth path is used.
class UnconfiguredSupabaseAuthGateway(SupabaseAuthGatewayProtocol):
    _MISSING_CREDENTIALS = "Supabase credentials are required for authentication."

    async def sign_in_with_password(self, email: str, password: str):
        raise NotConfiguredError(self._MISSING_CREDENTIALS)

    async def find_user_id_by_email(self, email: str):
        raise NotConfiguredError(self._MISSING_CREDENTIALS)

    async def create_user(
        self, *, email: str, password: str, metadata: dict | None = None
    ):
        raise NotConfiguredError(self._MISSING_CREDENTIALS)

    async def invite_user(
        self,
        *,
        email: str,
        metadata: dict | None = None,
        redirect_to: str | None = None,
    ):
        raise NotConfiguredError(self._MISSING_CREDENTIALS)

    async def generate_link(
        self,
        *,
        link_type: str,
        email: str,
        redirect_to: str | None = None,
        metadata: dict | None = None,
    ):
        raise NotConfiguredError(self._MISSING_CREDENTIALS)

    async def update_user_password(self, auth_user_id, password: str):
        raise NotConfiguredError(self._MISSING_CREDENTIALS)

    async def update_password_with_access_token(self, access_token: str, password: str):
        raise NotConfiguredError(self._MISSING_CREDENTIALS)

    async def update_password_with_token_hash(
        self, token_hash: str, verification_type: str, password: str
    ):
        raise NotConfiguredError(self._MISSING_CREDENTIALS)

    async def delete_user(self, auth_user_id):
        raise NotConfiguredError(self._MISSING_CREDENTIALS)

    async def aclose(self) -> None:
        return None


@dataclass(slots=True)
class ApplicationContainer:
    settings: Settings
    database_runtime_manager: DatabaseRuntimeManager
    session_factory: async_sessionmaker[AsyncSession]
    session_cookie_signer: SessionCookieSigner
    media_token_service: MediaTokenService
    auth_repository: DatabaseAuthRepository
    session_store: SessionStoreProtocol
    storage_service: StorageProtocol | None
    email_sender: EmailSenderProtocol
    email_outbox_service: EmailOutboxService
    supabase_auth_gateway: SupabaseAuthGatewayProtocol
    login_service: LoginService
    password_reset_service: PasswordResetService
    volunteers_service: VolunteersService
    role_assignments_service: RoleAssignmentsService
    groups_service: GroupsService
    courses_service: CoursesService
    shifts_service: ShiftsService
    shifts_setup_service: ShiftsSetupService
    shifts_app_service: ShiftsAppService
    volunteer_search_service: VolunteerSearchService
    admin_accounts_service: AdminAccountsService
    mobile_card_service: MobileCardService
    mobile_card_april_state_service: MobileCardAprilStateService
    rate_limiter: PostgresRateLimiter
    now_playing_service: NowPlayingService
    volunteer_applications_service: VolunteerApplicationsService
    semester_transfer_service: SemesterTransferService
    feedback_service: FeedbackService
    agent_chat_service: AgentChatService
    demo_guard: DemoDatabaseGuard

    async def aclose(self) -> None:
        if self.storage_service is not None:
            self.storage_service.close()
        await self.now_playing_service.aclose()
        await self.supabase_auth_gateway.aclose()
        await self.database_runtime_manager.aclose()


# Centralize the object graph here so the app, tests, and one-off scripts can
# swap an entire runtime configuration by replacing a single container object.
def build_application_container(
    settings: Settings | None = None,
) -> ApplicationContainer:
    resolved_settings = validate_production_secrets(settings or get_settings())
    database_runtime_manager = DatabaseRuntimeManager(resolved_settings)
    session_factory = database_runtime_manager.get_session_factory()
    session_cookie_signer = SessionCookieSigner(resolved_settings)
    media_token_service = MediaTokenService(resolved_settings)
    auth_repository = DatabaseAuthRepository(
        legacy_login_enabled=resolved_settings.legacy_admin_login_enabled
    )
    session_store = SessionStore(
        cast(SessionRepositoryProtocol, auth_repository), resolved_settings
    )
    storage_service = _build_storage_service(resolved_settings)
    supabase_auth_gateway = _build_supabase_auth_gateway(resolved_settings)
    email_sender = _build_email_sender(resolved_settings)
    mobile_card_email_renderer = MobileCardEmailTemplateRenderer()
    applicant_email_renderer = ApplicantEmailTemplateRenderer()
    admin_email_renderer = AdminAccountEmailTemplateRenderer()
    password_reset_email_renderer = PasswordResetEmailTemplateRenderer()
    email_outbox_service = EmailOutboxService(
        settings=resolved_settings,
        email_sender=email_sender,
        applicant_renderer=applicant_email_renderer,
    )
    rate_limiter = PostgresRateLimiter(session_factory=session_factory)
    demo_guard = DemoDatabaseGuard()
    mobile_card_april_state_service = MobileCardAprilStateService(
        repository=MobileCardAprilStateRepository()
    )

    volunteers_service = VolunteersService(
        repository=VolunteersRepository(),
        storage_service=storage_service,
        media_token_service=media_token_service,
        photo_processor=process_uploaded_photo,
        detail_cache_ttl_seconds=resolved_settings.volunteer_detail_cache_ttl_seconds,
        photo_upload_max_bytes=resolved_settings.photo_upload_max_bytes,
        photo_max_dimension=resolved_settings.photo_max_dimension,
    )
    volunteer_applications_service = VolunteerApplicationsService(
        settings=resolved_settings,
        volunteer_creator=volunteers_service,
        repository=VolunteerApplicationsRepository(
            media_token_service=media_token_service,
        ),
        email_outbox=email_outbox_service,
        storage_service=storage_service,
        photo_processor=process_uploaded_photo,
        pending_count_cache_ttl_seconds=resolved_settings.pending_volunteer_applications_cache_ttl_seconds,
    )

    role_assignments_service = RoleAssignmentsService(
        repository=RoleAssignmentsRepository(),
        invalidate_volunteer_cache=volunteers_service.invalidate_volunteer_cache,
    )
    container = ApplicationContainer(
        settings=resolved_settings,
        database_runtime_manager=database_runtime_manager,
        session_factory=session_factory,
        session_cookie_signer=session_cookie_signer,
        media_token_service=media_token_service,
        auth_repository=auth_repository,
        session_store=session_store,
        storage_service=storage_service,
        email_sender=email_sender,
        email_outbox_service=email_outbox_service,
        supabase_auth_gateway=supabase_auth_gateway,
        login_service=LoginService(
            repository=auth_repository,
            supabase_auth=supabase_auth_gateway,
            session_store=session_store,
        ),
        password_reset_service=PasswordResetService(
            auth_gateway=supabase_auth_gateway,
            email_sender=email_sender,
            email_renderer=password_reset_email_renderer,
            admin_account_repository=AdminAccountsRepository(),
        ),
        volunteers_service=volunteers_service,
        role_assignments_service=role_assignments_service,
        groups_service=GroupsService(),
        courses_service=CoursesService(repository=CoursesRepository()),
        shifts_service=ShiftsService(repository=ShiftsRepository()),
        shifts_setup_service=ShiftsSetupService(repository=ShiftsRepository()),
        shifts_app_service=ShiftsAppService(repository=ShiftsAppRepository()),
        volunteer_search_service=VolunteerSearchService(VolunteerSearchRepository()),
        admin_accounts_service=AdminAccountsService(
            repository=AdminAccountsRepository(),
            cache_ttl_seconds=resolved_settings.admin_accounts_cache_ttl_seconds,
            email_sender=email_sender,
            onboarding_email_renderer=admin_email_renderer,
        ),
        mobile_card_april_state_service=mobile_card_april_state_service,
        mobile_card_service=MobileCardService(
            resolved_settings,
            repository=MobileCardRepository(),
            email_sender=email_sender,
            rate_limiter=rate_limiter,
            media_token_service=media_token_service,
            april_state_service=mobile_card_april_state_service,
            email_template_renderer=mobile_card_email_renderer,
            trial_applicant_provider=volunteer_applications_service,
            demo_guard=demo_guard,
        ),
        rate_limiter=rate_limiter,
        now_playing_service=NowPlayingService(
            resolved_settings,
            repository=IntegrationTokensRepository(),
        ),
        volunteer_applications_service=volunteer_applications_service,
        semester_transfer_service=SemesterTransferService(),
        feedback_service=FeedbackService(resolved_settings, rate_limiter=rate_limiter),
        agent_chat_service=AgentChatService(
            resolved_settings,
            AgentChatRepository(),
            volunteers_service.invalidate_volunteer_cache,
            volunteers_service.count_volunteers,
            volunteers_service.list_volunteers,
            role_assignments_service,
            session_scope=lambda: session_scope(database_runtime_manager.get_runtime()),
        ),
        demo_guard=demo_guard,
    )

    return container


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    if not hasattr(app.state, "container"):
        app.state.container = build_application_container()
    await check_demo_database(app.state.container)
    try:
        yield
    finally:
        await app.state.container.aclose()


async def check_demo_database(container: ApplicationContainer) -> None:
    """With demo login on, refuse to start unless every person in the database is fake."""
    if not demo_login_enabled(container.settings):
        return
    async with session_scope(container.database_runtime_manager.get_runtime()):
        await container.demo_guard.ensure_only_fake_people()


def _build_storage_service(settings: Settings):
    if settings.azure_blob_connection_string:
        return StorageService(settings)
    if settings.app_env == "development":
        # Local harness: photos land in .devdata/photos so the upload
        # pipeline and media proxy work without Azure credentials.
        return LocalDirectoryStorage()
    return None


def _build_email_sender(settings: Settings) -> EmailSenderProtocol:
    if settings.app_env == "development" and not settings.resend_api_key:
        return ConsoleEmailSender()
    return ResendEmailSender(settings)


def _build_supabase_auth_gateway(settings: Settings) -> SupabaseAuthGatewayProtocol:
    if settings.supabase_url and settings.supabase_secret_key:
        return SupabaseAuthGateway(settings)
    if (
        settings.app_env == "development"
        and settings.dev_admin_email
        and settings.dev_admin_password
    ):
        return DevAuthGateway(settings)
    return UnconfiguredSupabaseAuthGateway()
