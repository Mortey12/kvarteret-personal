from __future__ import annotations

from app.observability import get_logger

import hmac
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from secrets import choice
from typing import Any, Protocol

from app.config import Settings
from app.db.rate_limit import RateLimiter, RateLimitExceeded
from app.db.session import commit_request_session
from app.domain.mobile_card.april_state import MobileCardAprilStateService
from app.domain.mobile_card.errors import (
    MobileCardDeliveryError,
    MobileCardDuplicatePersonError,
    MobileCardError,
    MobileCardInvalidAccessCodeError,
    MobileCardInvalidSessionError,
    MobileCardPersonNotFoundError,
    MobileCardRateLimitedError,
)
from app.domain.mobile_card.models import (
    DecodedMobileCardSession,
    MobileCardCurrentCardResult,
    MobileCardResponse,
    MobileCardRole,
    MobileCardRoleHistory,
    MobileCardSession,
)
from app.domain.mobile_card.repository import MobileCardRepository, MobileCardSnapshot
from app.domain.mobile_card.sessions import MobileCardSessionManager
from app.infrastructure.email.mobile_card_templates import (
    MobileCardEmailTemplateRenderer,
    MobileCardEmailTemplateRendererProtocol,
)
from app.infrastructure.email.protocols import EmailDeliveryError, EmailSenderProtocol
from app.media_tokens import MediaTokenService
from app.shared.semester import get_current_semester_code

# Re-export for backward compatibility
__all__ = [
    "DecodedMobileCardSession",
    "MobileCardCurrentCardResult",
    "MobileCardDuplicatePersonError",
    "MobileCardError",
    "MobileCardInvalidAccessCodeError",
    "MobileCardInvalidSessionError",
    "MobileCardPersonNotFoundError",
    "MobileCardRateLimitedError",
    "MobileCardResponse",
    "MobileCardRole",
    "MobileCardRoleHistory",
    "MobileCardService",
    "MobileCardSession",
]


logger = get_logger(__name__)


class TrialApplicantProviderProtocol(Protocol):
    async def find_active_trial_applicant_by_email(
        self, email: str, *, include_expired: bool = False
    ) -> Any | None: ...

    async def get_active_trial_applicant(
        self, application_id: int, *, include_expired: bool = False
    ) -> Any | None: ...

    async def get_approved_volunteer_id(self, application_id: int) -> int | None: ...


_LEGACY_PENGUIN_WORD_PREFIXES = [
    "bug",
    "mordi",
    "adelie",
    "bøyle",
    "dverg",
    "galápagos",
    "gulltop",
    "guløye",
    "horn",
    "humboldt",
    "hvitkinn",
    "kap",
    "keiser",
    "klippehopper",
    "konge",
    "langdusk",
    "magellan",
    "ring",
    "skog",
    "snares",
    "kode",
    "pode",
    "smart",
    "humor",
    "jule",
    "fjøs",
    "øl",
    "vin",
    "løpe",
    "party",
    "intern",
    "kaffe",
    "te",
    "kake",
    "pizza",
    "burger",
    "pasta",
    "taco",
    "sushi",
    "standup",
    "konsert",
    "quiz",
    "mikro",
    "økonomi",
    "fysikk",
    "matte",
    "kjemi",
    "biologi",
    "informatikk",
    "humaniora",
    "kor",
    "mugge",
]


class MobileCardService:
    def __init__(
        self,
        settings: Settings,
        repository: MobileCardRepository,
        email_sender: EmailSenderProtocol,
        rate_limiter: RateLimiter,
        media_token_service: MediaTokenService | None = None,
        april_state_service: MobileCardAprilStateService | None = None,
        email_template_renderer: MobileCardEmailTemplateRendererProtocol | None = None,
        trial_applicant_provider: TrialApplicantProviderProtocol | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.email_sender = email_sender
        self.rate_limiter = rate_limiter
        self.media_token_service = media_token_service
        self.april_state_service = april_state_service
        self.trial_applicant_provider = trial_applicant_provider
        self.email_template_renderer = (
            email_template_renderer or MobileCardEmailTemplateRenderer()
        )
        self.sessions = MobileCardSessionManager(settings)

    async def request_access_code(
        self, email: str, *, source_key: str | None = None, personal_login: bool = False
    ) -> None:
        normalized_email = email.strip().lower()
        if self._is_review_request(normalized_email, None):
            return None
        request_keys = _build_rate_limit_keys(normalized_email, source_key)
        await self._hit_rate_limits(
            "request",
            request_keys,
            limit=self.settings.mobile_card_access_code_request_limit,
            window_seconds=self.settings.mobile_card_access_code_request_window_seconds,
            message="Too many access-code requests. Try again later.",
        )

        volunteers = await self.repository.find_volunteers_by_email(normalized_email)
        trial_applicant = None
        if not volunteers and self.trial_applicant_provider is not None:
            trial_applicant = await self.trial_applicant_provider.find_active_trial_applicant_by_email(
                normalized_email, include_expired=True
            )
        if len(volunteers) > 1:
            raise MobileCardDuplicatePersonError(
                "More than one person uses this email address. Contact an administrator."
            )
        if not volunteers and trial_applicant is None:
            raise MobileCardPersonNotFoundError(
                "Email not found in the personnel database."
            )

        now = datetime.now(UTC)
        access_code = _generate_access_code()
        code_hash = self._hash_access_code(access_code)
        if volunteers:
            await self.repository.store_access_code(
                volunteer_id=volunteers[0]["id"], code_hash=code_hash, created_at=now
            )
            subject_id = volunteers[0]["id"]
            subject_type = "volunteer"
        else:
            assert trial_applicant is not None
            await self.repository.store_trial_access_code(
                application_id=trial_applicant.application_id,
                code_hash=code_hash,
                created_at=now,
            )
            subject_id = trial_applicant.application_id
            subject_type = "trial_application"

        # The code must be durably stored before the email announces it.
        await commit_request_session()

        render_email = (
            self.email_template_renderer.render_personal_access_code_email
            if personal_login
            else self.email_template_renderer.render_access_code_email
        )
        rendered_email = render_email(
            access_code=access_code,
            expires_in_minutes=self.settings.mobile_card_access_code_ttl_minutes,
        )
        try:
            await self.email_sender.send_email(
                recipient_email=email.strip(),
                subject=rendered_email.subject,
                html_body=rendered_email.html_body,
            )
        except EmailDeliveryError as exc:
            logger.exception(
                "mobile_card.access_code.delivery_failed",
                extra={"event": "mobile_card.access_code.delivery_failed"},
            )
            raise MobileCardDeliveryError(
                "Could not send access code email. Check your inbox before requesting another code."
            ) from exc
        logger.info(
            "mobile_card.access_code.sent",
            extra={
                "subject_type": subject_type,
                "subject_id": subject_id,
                "volunteer_id": subject_id if subject_type == "volunteer" else None,
            },
        )
        return None

    async def create_session(
        self,
        email: str,
        access_code: str,
        *,
        include_role_history: bool = False,
        source_key: str | None = None,
    ) -> MobileCardSession:
        normalized_email = email.strip().lower()
        normalized_access_code = access_code.strip()
        if self._is_review_request(normalized_email, normalized_access_code):
            card = self._build_review_card(include_role_history=include_role_history)
            token = self.sessions.build_token({"person_id": 0, "review": True})
            return MobileCardSession(session_token=token, card=card)
        attempt_keys = _build_rate_limit_keys(normalized_email, source_key)
        # Counts BEFORE validation to prevent TOCTOU races.
        await self._hit_rate_limits(
            "attempt",
            attempt_keys,
            limit=self.settings.mobile_card_session_attempt_limit,
            window_seconds=self.settings.mobile_card_session_attempt_window_seconds,
            message="Too many access-code attempts. Try again later.",
        )

        trial_applicant = None
        if self.trial_applicant_provider is not None:
            application_id = await self.repository.find_trial_application_for_code(
                email=normalized_email,
                code_hash=self._hash_access_code(normalized_access_code),
                expires_after=datetime.now(UTC)
                - timedelta(minutes=self.settings.mobile_card_access_code_ttl_minutes),
            )
            if application_id is not None:
                trial_applicant = (
                    await self.trial_applicant_provider.get_active_trial_applicant(
                        application_id, include_expired=True
                    )
                )
        volunteer_row = None
        if trial_applicant is not None:
            accepted = await self.repository.consume_trial_access_code(
                application_id=trial_applicant.application_id,
                code_hash=self._hash_access_code(normalized_access_code),
                expires_after=datetime.now(UTC)
                - timedelta(minutes=self.settings.mobile_card_access_code_ttl_minutes),
            )
            if not accepted:
                trial_applicant = None
        if trial_applicant is None:
            volunteer_row = await self.repository.find_volunteer_by_email_and_code(
                email=normalized_email,
                code_hash=self._hash_access_code(normalized_access_code),
                expires_after=datetime.now(UTC)
                - timedelta(minutes=self.settings.mobile_card_access_code_ttl_minutes),
            )
        if volunteer_row is None and trial_applicant is None:
            raise MobileCardInvalidAccessCodeError("Invalid access code.")
        await self.rate_limiter.clear(
            *(f"mobile-card:attempt:{key}" for key in attempt_keys)
        )
        if volunteer_row is not None:
            token = self.sessions.build_token({"person_id": volunteer_row["id"]})
            card = await self._build_card(
                volunteer_row["id"], include_role_history=include_role_history
            )
        else:
            assert trial_applicant is not None
            token = self.sessions.build_token(
                {"trial_application_id": trial_applicant.application_id}
            )
            card = self._build_trial_card(trial_applicant)
        logger.info(
            "mobile_card.session.created",
            extra={
                "subject_type": "volunteer"
                if volunteer_row is not None
                else "trial_application",
                "subject_id": volunteer_row["id"]
                if volunteer_row is not None
                else trial_applicant.application_id,
                "volunteer_id": volunteer_row["id"] if volunteer_row is not None else None,
            },
            after_commit=True,
        )
        return MobileCardSession(session_token=token, card=card)

    async def get_current_card(
        self, session_token: str, *, include_role_history: bool = False
    ) -> MobileCardCurrentCardResult:
        decoded = self.sessions.decode_token(session_token)

        promoted_session_token = None
        if decoded.is_review:
            card = self._build_review_card(include_role_history=include_role_history)
        elif decoded.trial_application_id is not None:
            if self.trial_applicant_provider is None:
                raise MobileCardInvalidAccessCodeError("Trial access has expired.")
            trial_applicant = (
                await self.trial_applicant_provider.get_active_trial_applicant(
                    decoded.trial_application_id, include_expired=True
                )
            )
            if trial_applicant is None:
                volunteer_id = (
                    await self.trial_applicant_provider.get_approved_volunteer_id(
                        decoded.trial_application_id
                    )
                )
                if volunteer_id is None:
                    raise MobileCardInvalidAccessCodeError("Trial access has expired.")
                card = await self._build_card(
                    volunteer_id, include_role_history=include_role_history
                )
                promoted_session_token = self.sessions.build_token(
                    {"person_id": volunteer_id}
                )
            else:
                card = self._build_trial_card(trial_applicant)
        else:
            card = await self._build_card(
                decoded.person_id or 0, include_role_history=include_role_history
            )

        renewed_session_token = promoted_session_token or self.sessions.maybe_renew(
            decoded
        )
        return MobileCardCurrentCardResult(
            card=card,
            renewed_session_token=renewed_session_token,
        )

    def _build_trial_card(self, snapshot: Any) -> MobileCardResponse:
        photo_url = (
            self.media_token_service.build_photo_media_url(snapshot.photo_path)
            if self.media_token_service is not None and snapshot.photo_path
            else None
        )
        # An expired trial still gets a card so the app can explain why the ID
        # is not valid, but it carries no roles, discounts or photo.
        ends_at = snapshot.trial_ends_at
        if ends_at.tzinfo is None:  # SQLite (tests) returns naive datetimes
            ends_at = ends_at.replace(tzinfo=UTC)
        expired = ends_at <= datetime.now(UTC)
        return MobileCardResponse(
            person_id=snapshot.volunteer_id or -snapshot.application_id,
            first_name=snapshot.first_name,
            last_name=snapshot.last_name,
            birth_date=snapshot.birth_date,
            created_at=snapshot.created_at,
            valid_until=snapshot.trial_ends_at,
            photo_url=None if expired else photo_url,
            pingvin_points=0,
            probation_expired=True if expired else None,
            active_roles=[]
            if expired
            else [
                MobileCardRole(
                    name=f"{snapshot.role_name} (Prøvetid)",
                    group=snapshot.group_name,
                    discount_level=snapshot.discount_level,
                    pingvin_points=0,
                    signed_contract=False,
                )
            ],
            role_history=None,
            word_of_the_day=_word_of_the_day(),
        )

    async def _build_card(
        self, volunteer_id: int, *, include_role_history: bool = False
    ) -> MobileCardResponse:
        current_semester = get_current_semester_code()
        snapshot = await self.repository.fetch_card_snapshot(
            volunteer_id=volunteer_id,
            semester_code=current_semester,
            include_role_history=include_role_history,
        )
        if snapshot is None:
            raise MobileCardPersonNotFoundError(
                f"Volunteer {volunteer_id} was not found."
            )
        return await self._build_card_response(
            snapshot, include_role_history=include_role_history
        )

    async def _build_card_response(
        self, snapshot: MobileCardSnapshot, *, include_role_history: bool = False
    ) -> MobileCardResponse:
        photo_url = (
            self.media_token_service.build_photo_media_url(snapshot.photo_path)
            if snapshot.photo_path and self.media_token_service is not None
            else None
        )
        if (
            self.april_state_service is not None
            and await self.april_state_service.is_enabled()
        ):
            photo_url = None
        return MobileCardResponse(
            person_id=snapshot.volunteer_id,
            first_name=snapshot.first_name,
            last_name=snapshot.last_name,
            birth_date=snapshot.birth_date,
            created_at=snapshot.created_at,
            valid_until=datetime.now(UTC)
            + timedelta(days=self.settings.mobile_card_session_ttl_days),
            photo_url=photo_url,
            pingvin_points=snapshot.pingvin_points,
            active_roles=[
                MobileCardRole(
                    name=role.name,
                    group=role.group,
                    discount_level=role.discount_level,
                    pingvin_points=role.pingvin_points,
                    signed_contract=role.signed_contract,
                )
                for role in snapshot.active_roles
            ],
            role_history=(
                [
                    MobileCardRoleHistory(
                        name=role.name,
                        group=role.group,
                        discount_level=role.discount_level,
                        pingvin_points=role.pingvin_points,
                        signed_contract=role.signed_contract,
                        year=role.semester // 10,
                        term=role.semester % 10,
                        semester=_semester_label(role.semester),
                        is_active=role.is_active,
                    )
                    for role in snapshot.role_history
                ]
                if include_role_history
                else None
            ),
            word_of_the_day=_word_of_the_day(),
        )

    def _is_review_request(self, email: str, access_code: str | None) -> bool:
        if not self.settings.review_bypass_enabled:
            return False
        if (
            not self.settings.review_bypass_email
            or not self.settings.review_bypass_token
        ):
            return False
        return email == self.settings.review_bypass_email.lower() and (
            access_code is None or access_code == self.settings.review_bypass_token
        )

    def _build_review_card(
        self, *, include_role_history: bool = False
    ) -> MobileCardResponse:
        return MobileCardResponse(
            person_id=0,
            first_name="Review",
            last_name="User",
            birth_date=date(1990, 1, 1),
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            valid_until=datetime.now(UTC) + timedelta(days=365),
            photo_url=None,
            pingvin_points=42,
            active_roles=[
                MobileCardRole(
                    name="Guest",
                    group="Kvarteret",
                    discount_level=0,
                    pingvin_points=0,
                    signed_contract=True,
                )
            ],
            role_history=(
                [
                    MobileCardRoleHistory(
                        name="Guest",
                        group="Kvarteret",
                        discount_level=0,
                        pingvin_points=0,
                        signed_contract=True,
                        year=datetime.now(UTC).year,
                        term=1 if datetime.now(UTC).month <= 6 else 2,
                        semester="Review",
                        is_active=True,
                    )
                ]
                if include_role_history
                else None
            ),
            word_of_the_day=_word_of_the_day(),
        )

    async def _hit_rate_limits(
        self,
        kind: str,
        keys: tuple[str, ...],
        *,
        limit: int,
        window_seconds: int,
        message: str,
    ) -> None:
        if limit < 1:
            return
        try:
            for key in keys:
                await self.rate_limiter.hit(
                    f"mobile-card:{kind}:{key}",
                    limit=limit,
                    window_seconds=window_seconds,
                )
        except RateLimitExceeded:
            raise MobileCardRateLimitedError(message) from None

    def _hash_access_code(self, access_code: str) -> str:
        # HMAC keyed by the app secret: a 6-digit code is trivially
        # brute-forced offline from a bare digest in a database leak.
        return hmac.new(
            self.settings.app_secret_key.encode("utf-8"),
            access_code.encode("utf-8"),
            sha256,
        ).hexdigest()


def _generate_access_code(length: int = 6) -> str:
    digits = "0123456789"
    return "".join(choice(digits) for _ in range(length))


def _build_rate_limit_keys(email: str, source_key: str | None) -> tuple[str, ...]:
    keys = [f"email:{email}"]
    if source_key:
        keys.append(f"source:{source_key}")
        keys.append(f"email-source:{email}:{source_key}")
    return tuple(keys)


def _word_of_the_day(now: datetime | None = None) -> str:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    else:
        current_time = current_time.astimezone(UTC)
    current_day = current_time.date()
    if current_time < datetime.combine(current_day, time(hour=4), tzinfo=UTC):
        current_day -= timedelta(days=1)
    word_index = _daily_word_index(current_day)
    return f"{_LEGACY_PENGUIN_WORD_PREFIXES[word_index]}pingvin"


def _semester_label(semester_code: int) -> str:
    term = semester_code % 10
    if term == 1:
        return "Vår"
    if term == 2:
        return "Høst"
    return str(semester_code)


def _daily_word_index(current_day: date) -> int:
    digest = sha256(current_day.isoformat().encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % len(_LEGACY_PENGUIN_WORD_PREFIXES)
