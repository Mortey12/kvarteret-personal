from __future__ import annotations

from app.observability import get_logger

from urllib.parse import parse_qs, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature
from pydantic import ValidationError

from app.auth.roles import UserRole
from app.auth.models import AuthenticatedUser
from app.auth.cookies import SessionCookieSigner
from app.auth.demo_login import (
    DEMO_LEADER_EMAIL,
    DemoLoginRefused,
    demo_code_matches,
    demo_login_enabled,
    log_demo_login,
)
from app.auth.login_service import LoginError, LoginService
from app.auth.supabase_auth import RecoveryTokenError
from app.auth.password_reset_service import (
    PasswordResetRequest,
    PasswordResetServiceProtocol,
    password_reset_account_key,
    password_reset_ip_key,
)
from app.db.rate_limit import RateLimiter, RateLimitExceeded
from app.dependencies import (
    get_auth_repository,
    get_current_user,
    get_demo_guard,
    get_login_service,
    get_mobile_card_april_state_service,
    get_mobile_card_service,
    get_admin_accounts_service,
    get_password_reset_service,
    get_rate_limiter,
    get_session_cookie_signer,
    get_session_store,
    get_settings,
    get_supabase_auth_gateway,
    require_authenticated_user,
)
from app.observability import client_ip_from_request
from app.errors import NotConfiguredError
from app.observability import log_admin_activity
from app.domain.mobile_card.april_state import MobileCardAprilStateService
from app.domain.mobile_card.errors import (
    MobileCardError, MobileCardPersonNotFoundError, MobileCardDuplicatePersonError,
    MobileCardRateLimitedError,
)
from app.web.cookies import resolve_cookie_domain
from app.web.templates import templates

_LOGIN_TEMPLATE = "pages/auth/login.html"
_FORGOT_PASSWORD_TEMPLATE = "pages/auth/forgot_password.html"
_PASSWORD_RESET_SENT_MESSAGE = (
    "Hvis e-postadressen tilhører en konto, har vi sendt en lenke for å "
    "tilbakestille passordet."
)

router = APIRouter()


def _app_login_response(request, *, error=None, message=None, next_path='/', code_email=None, status_code=200):
    return templates.TemplateResponse(request, _LOGIN_TEMPLATE, dict(
        title='Logg inn', section='login', error_message=error, message=message,
        next_path=_safe_login_redirect(next_path), app_code_email=code_email), status_code=status_code)


@router.post('/login/app/code')
async def request_app_login_code(
    request: Request, email: str = Form(...), next: str = Form(default='/'),
    service=Depends(get_mobile_card_service),
):
    try:
        await service.request_access_code(email, source_key=client_ip_from_request(request), personal_login=True)
    except (MobileCardPersonNotFoundError, MobileCardDuplicatePersonError):
        pass
    except MobileCardRateLimitedError:
        return _app_login_response(request, error='For mange forsøk. Prøv igjen senere.', status_code=429, next_path=next)
    except (MobileCardError, NotConfiguredError):
        return _app_login_response(request, error='Kunne ikke sende kode. Prøv igjen senere.', status_code=503, next_path=next)
    return _app_login_response(request, message='Sjekk din e-post!', code_email=email, next_path=next)


@router.post('/login/app')
async def app_login(
    request: Request, email: str = Form(...), access_code: str = Form(...), next: str = Form(default='/'),
    service=Depends(get_mobile_card_service), admin_accounts_service=Depends(get_admin_accounts_service),
    session_store=Depends(get_session_store), settings=Depends(get_settings),
    session_cookie_signer=Depends(get_session_cookie_signer),
    current_user=Depends(get_current_user),
):
    # A retried form must not turn a successful login into a used-code error.
    if current_user is not None and current_user.email.strip().casefold() == email.strip().casefold():
        return RedirectResponse(_safe_login_redirect(next), status_code=303)
    try:
        mobile_session = await service.create_session(email, access_code, source_key=client_ip_from_request(request))
        subject = service.sessions.decode_token(mobile_session.session_token)
        # Store-review identities and trial applicants cannot establish admin sessions.
        if subject.is_review or subject.person_id is None or subject.person_id <= 0 or subject.trial_application_id is not None:
            raise ValueError('Denne app-brukeren har ikke tilgang til adminpanelet.')
        account = await admin_accounts_service.get_individual_account(subject.person_id)
        if account is None or account.is_legacy_account:
            raise ValueError('Denne app-brukeren har ikke tilgang til adminpanelet.')
        session = await session_store.create_session(auth_user_id=account.auth_user_id,
            user_account_id=account.user_account_id, ip_address=client_ip_from_request(request),
            user_agent=request.headers.get('user-agent'))
    except MobileCardRateLimitedError:
        return _app_login_response(request, error='For mange forsøk. Prøv igjen senere.', status_code=429, code_email=email, next_path=next)
    except (MobileCardError, ValueError):
        return _app_login_response(request, error='Ugyldig kode eller manglende tilgang til adminpanelet.', status_code=401, code_email=email, next_path=next)
    except NotConfiguredError:
        return _app_login_response(request, error='Innlogging er ikke tilgjengelig akkurat nå.', status_code=503, code_email=email, next_path=next)
    response = RedirectResponse(_safe_login_redirect(next), status_code=303)
    _set_session_cookie(response, request=request, settings=settings, session_cookie_signer=session_cookie_signer, session_id=session.session_id)
    log_admin_activity(request=request, user=AuthenticatedUser(
        auth_user_id=account.auth_user_id, user_account_id=account.user_account_id,
        username=account.username, email=account.email, display_name=account.display_name, role=account.role),
        action='auth.app_login', subject_type='admin_account', subject_id=account.user_account_id)
    return response
@router.post('/login/demo')
async def demo_leader_login(
    request: Request, access_code: str = Form(...), next: str = Form(default='/shifts'),
    settings=Depends(get_settings), rate_limiter: RateLimiter = Depends(get_rate_limiter),
    demo_guard=Depends(get_demo_guard), auth_repository=Depends(get_auth_repository),
    session_store=Depends(get_session_store),
    session_cookie_signer: SessionCookieSigner = Depends(get_session_cookie_signer),
):
    """Hosted demo only (app/auth/demo_login.py): the demo leader, as Admin, with the demo code."""
    if not demo_login_enabled(settings):
        raise HTTPException(status_code=404)
    client_ip = client_ip_from_request(request)
    throttle_keys = [f"login:account:{DEMO_LEADER_EMAIL}"] + ([f"login:ip:{client_ip}"] if client_ip else [])
    try:
        for key in throttle_keys:
            await rate_limiter.hit(key, limit=settings.login_attempt_limit,
                                   window_seconds=settings.login_attempt_window_seconds)
    except RateLimitExceeded:
        return _app_login_response(request, error='For mange forsøk. Prøv igjen senere.', status_code=429, next_path=next)
    if not demo_code_matches(settings, access_code):
        return _app_login_response(request, error='Feil demokode.', status_code=401, next_path=next)
    try:
        await demo_guard.ensure_only_fake_people()
    except DemoLoginRefused:
        return _app_login_response(request, error='Demo-innlogging er stengt.', status_code=503, next_path=next)
    account = await auth_repository.get_user_account_by_identifier(DEMO_LEADER_EMAIL)
    if account is None or account.email.strip().lower() != DEMO_LEADER_EMAIL or account.role != UserRole.ADMIN:
        return _app_login_response(request, error='Demo-lederen finnes ikke. Kjør scripts/dev/seed_demo.py.',
                                   status_code=503, next_path=next)
    session = await session_store.create_session(auth_user_id=account.auth_user_id, user_account_id=account.id,
        ip_address=client_ip, user_agent=request.headers.get('user-agent'))
    response = RedirectResponse(_safe_login_redirect(next), status_code=303)
    _set_session_cookie(response, request=request, settings=settings, session_cookie_signer=session_cookie_signer, session_id=session.session_id)
    log_demo_login(account.email, kind='leader')
    log_admin_activity(request=request, user=AuthenticatedUser(
        auth_user_id=account.auth_user_id, user_account_id=account.id, username=account.username,
        email=account.email, display_name=account.display_name, role=account.role),
        action='auth.demo_login', subject_type='admin_account', subject_id=account.id)
    return response


_APRIL_TOGGLE_EMAIL = "it.leder@kvarteret.no"
logger = get_logger(__name__)
_ORAKEL_ORIGIN = "https://orakel.samfunnetibergen.no"
_ORAKEL_HOST = urlsplit(_ORAKEL_ORIGIN).hostname


def _safe_login_redirect(next_url: str | None) -> str:
    """Allow only local paths or the Orakel origin as a post-login target."""
    if not next_url:
        return "/"
    parsed = urlsplit(next_url)
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme != "https"
            or parsed.hostname != _ORAKEL_HOST
            or parsed.username is not None
            or parsed.password is not None
        ):
            return "/"
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, parsed.fragment)
        )
    if not next_url.startswith("/") or next_url.startswith("//"):
        return "/"
    return next_url


def _login_redirect_from_request(request: Request, next_url: str | None) -> str:
    if next_url:
        return _safe_login_redirect(next_url)
    referer = request.headers.get("referer")
    if referer:
        referer_query = parse_qs(urlsplit(referer).query)
        return _safe_login_redirect(referer_query.get("next", [None])[0])
    return "/"


def _set_session_cookie(
    response,
    *,
    request: Request,
    settings,
    session_cookie_signer: SessionCookieSigner,
    session_id: str,
) -> None:
    cookie_domain = resolve_cookie_domain(request, settings.session_cookie_domain)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_cookie_signer.sign_session_id(session_id),
        httponly=True,
        secure=request.url.scheme == "https" or settings.app_env == "production",
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        **({"domain": cookie_domain} if cookie_domain else {}),
    )


@router.get("/")
async def dashboard(
    request: Request,
    mobile_card_april_message: str | None = None,
    current_user=Depends(get_current_user),
    mobile_card_april_state_service: MobileCardAprilStateService = Depends(
        get_mobile_card_april_state_service
    ),
):
    if current_user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    show_mobile_card_april_toggle = (
        current_user.role == UserRole.ADMIN
        and current_user.email.strip().lower() == _APRIL_TOGGLE_EMAIL
    )
    mobile_card_april_enabled = False
    if show_mobile_card_april_toggle:
        mobile_card_april_enabled = await mobile_card_april_state_service.is_enabled()
    return templates.TemplateResponse(
        request,
        "pages/auth/dashboard.html",
        {
            "title": "Kvarteret Personal",
            "section": "dashboard",
            "current_user": current_user,
            "mobile_card_april_enabled": mobile_card_april_enabled,
            "mobile_card_april_message": mobile_card_april_message,
            "show_mobile_card_april_toggle": show_mobile_card_april_toggle,
        },
    )


@router.get("/login")
async def login_page(
    request: Request,
    message: str | None = None,
    next: str | None = None,
    current_user=Depends(get_current_user),
):
    if current_user is not None:
        return RedirectResponse(
            url=_safe_login_redirect(next), status_code=status.HTTP_303_SEE_OTHER
        )
    return templates.TemplateResponse(
        request,
        _LOGIN_TEMPLATE,
        {
            "title": "Login",
            "section": "login",
            "error_message": None,
            "message": message,
            "next_path": _safe_login_redirect(next),
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(default=None),
    login_service: LoginService = Depends(get_login_service),
    session_cookie_signer: SessionCookieSigner = Depends(get_session_cookie_signer),
    settings=Depends(get_settings),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
):
    login_redirect = _login_redirect_from_request(request, next)
    normalized_identifier = identifier.strip().lower()
    client_ip = client_ip_from_request(request)
    throttle_keys = [f"login:account:{normalized_identifier}"]
    if client_ip:
        throttle_keys.append(f"login:ip:{client_ip}")
    try:
        # Counted before verification so failures cannot race the check.
        for key in throttle_keys:
            await rate_limiter.hit(
                key,
                limit=settings.login_attempt_limit,
                window_seconds=settings.login_attempt_window_seconds,
            )
    except RateLimitExceeded:
        logger.warning(
            "login throttled",
            extra={
                "event": "auth.login.throttled",
                "event_data": {"identifier": normalized_identifier},
            },
        )
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            {
                "title": "Login",
                "section": "login",
                "error_message": "For mange innloggingsforsøk. Prøv igjen senere.",
                "message": None,
                "next_path": login_redirect,
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    try:
        result = await login_service.login(
            identifier=identifier,
            password=password,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except NotConfiguredError:
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            {
                "title": "Login",
                "section": "login",
                "error_message": "Innlogging er ikke konfigurert.",
                "message": None,
                "next_path": login_redirect,
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except LoginError:
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            {
                "title": "Login",
                "section": "login",
                "error_message": "Feil brukernavn eller passord.",
                "message": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    response = RedirectResponse(
        url=login_redirect, status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session_cookie(
        response,
        request=request,
        settings=settings,
        session_cookie_signer=session_cookie_signer,
        session_id=result.session.session_id,
    )
    return response


@router.get("/forgot-password")
async def forgot_password_page(
    request: Request,
    sent: bool = False,
    current_user=Depends(get_current_user),
):
    if current_user is not None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        _FORGOT_PASSWORD_TEMPLATE,
        {
            "title": "Glemt passord",
            "section": "login",
            "error_message": None,
            "message": _PASSWORD_RESET_SENT_MESSAGE if sent else None,
            "email": "",
        },
    )


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    password_reset_service: PasswordResetServiceProtocol = Depends(
        get_password_reset_service
    ),
    settings=Depends(get_settings),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
):
    try:
        reset_request = PasswordResetRequest(email=email)
    except ValidationError:
        return templates.TemplateResponse(
            request,
            _FORGOT_PASSWORD_TEMPLATE,
            {
                "title": "Glemt passord",
                "section": "login",
                "error_message": "Skriv inn en gyldig e-postadresse.",
                "message": None,
                "email": email.strip(),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    normalized_email = str(reset_request.email).lower()
    throttle_keys = [
        password_reset_account_key(normalized_email, settings.app_secret_key)
    ]
    client_ip = client_ip_from_request(request)
    if client_ip:
        throttle_keys.append(password_reset_ip_key(client_ip, settings.app_secret_key))
    try:
        for key in throttle_keys:
            await rate_limiter.hit(
                key,
                limit=settings.password_reset_attempt_limit,
                window_seconds=settings.password_reset_attempt_window_seconds,
            )
    except RateLimitExceeded:
        return templates.TemplateResponse(
            request,
            _FORGOT_PASSWORD_TEMPLATE,
            {
                "title": "Glemt passord",
                "section": "login",
                "error_message": "For mange forespørsler. Prøv igjen senere.",
                "message": None,
                "email": normalized_email,
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    origin = (settings.app_public_base_url or str(request.base_url)).rstrip("/")
    try:
        await password_reset_service.send_reset_email(
            email=normalized_email,
            redirect_to=f"{origin}/set-password",
        )
    except Exception:
        # The response deliberately remains identical for unknown accounts and
        # delivery failures to prevent account enumeration.
        logger.exception("auth.password_reset.delivery_failed", extra={"event": "auth.password_reset.delivery_failed"})
    return RedirectResponse(
        url="/forgot-password?sent=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/logout")
async def logout(
    request: Request,
    current_user=Depends(get_current_user),
    session_cookie_signer: SessionCookieSigner = Depends(get_session_cookie_signer),
    settings=Depends(get_settings),
    session_store=Depends(get_session_store),
):
    signed_cookie = request.cookies.get(settings.session_cookie_name)
    if signed_cookie:
        try:
            session_id = session_cookie_signer.unsign_session_id(signed_cookie)
            await session_store.delete_session(session_id)
        except BadSignature:
            logger.warning("auth.logout.invalid_cookie_discarded", extra={"event": "auth.logout.invalid_cookie_discarded"})
        except Exception:
            logger.exception("auth.logout.revocation_failed", extra={"event": "auth.logout.revocation_failed"})
    if current_user is not None and current_user.role == UserRole.ADMIN:
        log_admin_activity(
            request=request,
            user=current_user,
            action="logout",
            subject_type="session",
            subject_id=getattr(
                getattr(request.state, "session", None), "session_id", None
            ),
        )
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    cookie_domain = resolve_cookie_domain(request, settings.session_cookie_domain)
    response.delete_cookie(
        settings.session_cookie_name,
        **({"domain": cookie_domain} if cookie_domain else {}),
    )
    return response


@router.get("/set-password")
async def set_password_page(
    request: Request,
    error: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "pages/auth/set_password.html",
        {
            "title": "Set Password",
            "section": "set-password",
            "error_message": error,
            "access_token": None,
            "token_hash": None,
            "verification_type": None,
        },
    )


@router.post("/set-password")
async def set_password_submit(
    request: Request,
    access_token: str = Form(default=""),
    token_hash: str = Form(default=""),
    verification_type: str = Form(default=""),
    password: str = Form(...),
    confirm_password: str = Form(...),
    supabase_auth_gateway=Depends(get_supabase_auth_gateway),
    admin_accounts_service=Depends(get_admin_accounts_service),
):
    error_message = None
    normalized_access_token = access_token.strip()
    normalized_token_hash = token_hash.strip()
    normalized_verification_type = verification_type.strip()
    if not normalized_access_token and not normalized_token_hash:
        error_message = "Password setup link is missing or invalid."
    elif normalized_token_hash and normalized_verification_type != "recovery":
        error_message = "Password setup link is missing or invalid."
    elif len(password) < 8:
        error_message = "Passordet må være minst 8 tegn."
    elif password != confirm_password:
        error_message = "Passordene må være like."
    else:
        try:
            if normalized_token_hash:
                auth_user_id = await supabase_auth_gateway.update_password_with_token_hash(
                    normalized_token_hash,
                    normalized_verification_type,
                    password,
                )
            else:
                auth_user_id = await supabase_auth_gateway.update_password_with_access_token(
                    normalized_access_token, password
                )
            if auth_user_id is not None:
                await admin_accounts_service.mark_onboarding_complete(auth_user_id)
        except RecoveryTokenError:
            logger.info("auth.recovery.rejected", extra={"event": "auth.recovery.rejected"})
            error_message = (
                "Denne lenken er utløpt eller allerede brukt. Be om en ny lenke "
                "for å sette passordet på nytt."
            )
        except Exception:
            logger.exception("auth.onboarding.password_failed", extra={"event": "auth.onboarding.password_failed"})
            error_message = "Kunne ikke sette passordet akkurat nå."
    if error_message is not None:
        return templates.TemplateResponse(
            request,
            "pages/auth/set_password.html",
            {
                "title": "Set Password",
                "section": "set-password",
                "error_message": error_message,
                "access_token": normalized_access_token,
                "token_hash": normalized_token_hash,
                "verification_type": normalized_verification_type,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse(
        url="/login?message=Passordet+er+satt.+Du+kan+logge+inn+na.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/impersonation/stop")
async def stop_impersonation(
    request: Request,
    current_user=Depends(require_authenticated_user),
    session_cookie_signer: SessionCookieSigner = Depends(get_session_cookie_signer),
    settings=Depends(get_settings),
    session_store=Depends(get_session_store),
):
    session = getattr(request.state, "session", None)
    impersonator_user = getattr(session, "impersonator_user", None)
    if session is None or impersonator_user is None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    new_session = await session_store.create_session(
        auth_user_id=impersonator_user.auth_user_id,
        user_account_id=impersonator_user.user_account_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await session_store.delete_session(session.session_id)
    log_admin_activity(
        request=request,
        user=impersonator_user,
        action="admin_account.stop_impersonation",
        subject_type="admin_account",
        subject_id=current_user.user_account_id,
        details={"impersonated_user_account_id": current_user.user_account_id},
    )
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(
        response,
        request=request,
        settings=settings,
        session_cookie_signer=session_cookie_signer,
        session_id=new_session.session_id,
    )
    return response
