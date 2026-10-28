from time import perf_counter

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
import re
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature

from starlette.types import Message
from opentelemetry import trace

from app.api.router import api_router
from app.db.session import (
    reset_request_session,
    rollback_request_session,
    set_request_session,
)
from app.error_tracking import begin_error_tracking_request, end_error_tracking_request
from app.errors import NotConfiguredError
from app.media.router import router as media_router
from app.internal.router import router as internal_router
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.canonical_web import CanonicalWebMiddleware
from app.observability import (
    get_logger,
    bind_request_context,
    build_request_id,
    clear_request_context,
    configure_logging,
    diagnostic_session_id,
    log_request,
    log_request_exception,
    request_context_for_user,
    reset_request_context,
)
from app.config import Settings
from app.runtime import app_lifespan, build_application_container
from app.telemetry import configure_telemetry
from app.system.router import router as system_router
from app.web.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_FIELD_NAME,
    CSRF_HEADER_NAME,
    CsrfTokenService,
)
from app.web.router import web_router

logger = get_logger(__name__)


def create_app(container=None) -> FastAPI:
    resolved_container = container or build_application_container()
    configure_logging(resolved_container.settings)
    app = FastAPI(title="Kvarteret Personal", lifespan=app_lifespan)
    app.state.container = resolved_container
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    _install_security_headers(app)
    _install_method_override_middleware(app)
    _install_csrf_middleware(app, resolved_container)
    _install_auth_context_middleware(app, resolved_container)
    _install_request_session_middleware(app, resolved_container)
    _install_request_context_middleware(app)
    _install_http_exception_handler(app)
    _include_routers(app)
    app.add_middleware(CanonicalWebMiddleware)
    configure_telemetry(
        app,
        resolved_container.settings,
        database_engine=_database_engine(resolved_container),
    )
    return app


def _database_engine(container):
    manager = getattr(container, "database_runtime_manager", None)
    if manager is None:
        return None
    try:
        return manager.get_runtime().engine
    except NotConfiguredError:
        return None


def _install_method_override_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def method_override(request: Request, call_next):
        if request.method == "POST":
            override = request.query_params.get("_method") or request.headers.get(
                "X-HTTP-Method-Override"
            )
            if override:
                request.scope["method"] = override.upper()
        return await call_next(request)


def _install_csrf_middleware(app: FastAPI, container) -> None:
    csrf_token_service = CsrfTokenService(container.settings)

    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next):
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
        csrf_cookie_is_valid = csrf_token_service.is_valid_token(csrf_cookie)
        request.state.csrf_token = (
            csrf_cookie if csrf_cookie_is_valid else csrf_token_service.issue_token()
        )
        request.state.csrf_cookie_needs_refresh = not csrf_cookie_is_valid

        if _requires_csrf_validation(request):
            submitted_token = await _load_submitted_csrf_token(request)
            if not csrf_token_service.tokens_match(csrf_cookie, submitted_token):
                return Response(status_code=403, content="CSRF validation failed.")

        response = await call_next(request)
        if request.state.csrf_cookie_needs_refresh:
            csrf_token_service.set_cookie(response, request, request.state.csrf_token)
        return response


def _install_request_session_middleware(app: FastAPI, container) -> None:
    """One AsyncSession per HTTP request — the unit of work.

    Installed outside the auth-context middleware so session loading from
    the database shares the request session. The session is lazy: no
    connection is opened until the first statement executes, so requests
    that never touch the database (static files, /health) cost nothing.

    Commits on success, rolls back on exception. Workflows that fire
    external side effects commit earlier via ``commit_request_session()``.
    """

    @app.middleware("http")
    async def request_session_middleware(request: Request, call_next):
        try:
            session_factory = container.database_runtime_manager.get_session_factory()
        except NotConfiguredError:
            # DB-less environments (some tests, partial local setups):
            # proceed without a session; repositories raise on first use.
            return await call_next(request)

        async with session_factory() as session:
            token = set_request_session(session)
            try:
                response = await call_next(request)
            except Exception:
                await session.rollback()
                raise
            else:
                if session.in_transaction():
                    await session.commit()
                return response
            finally:
                reset_request_session(token)


def _install_auth_context_middleware(app: FastAPI, container) -> None:
    @app.middleware("http")
    async def auth_context_middleware(request: Request, call_next):
        request.state.current_user = None
        request.state.session = None
        request.state.impersonator_user = None
        request.state.volunteer_application_pending_count = 0
        signed_cookie = request.cookies.get(container.settings.session_cookie_name)
        if signed_cookie:
            try:
                session_id = container.session_cookie_signer.unsign_session_id(
                    signed_cookie
                )
                auth_context = await container.session_store.load_authenticated_user(
                    session_id
                )
                if auth_context:
                    request.state.session, request.state.current_user = auth_context
                    request.state.impersonator_user = (
                        request.state.session.impersonator_user
                    )
                    bind_request_context(
                        **request_context_for_user(request.state.current_user)
                    )
            except BadSignature:
                request.state.current_user = None
                request.state.session = None
                request.state.impersonator_user = None
            except Exception:
                logger.exception(
                    "auth.context.unavailable",
                    extra={
                        "event": "auth.context.unavailable",
                        "event_data": {"failure_stage": "auth_session_load"},
                    },
                )
                request.state.current_user = None
                request.state.session = None
                request.state.impersonator_user = None
                await rollback_request_session()
                message = "Innlogging er midlertidig utilgjengelig. Prøv igjen om litt."
                if request.url.path.startswith("/api/"):
                    return JSONResponse(
                        {"detail": message},
                        status_code=503,
                        headers={"Retry-After": "30"},
                    )
                return Response(
                    message,
                    status_code=503,
                    media_type="text/plain",
                    headers={"Retry-After": "30"},
                )
        return await call_next(request)


def _install_request_context_middleware(app: FastAPI) -> None:
    settings = (
        app.state.container.settings
        if hasattr(app.state, "container")
        else Settings(_env_file=None)
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        started_at = perf_counter()
        request_id = build_request_id(request)
        session_id = diagnostic_session_id(request, settings)
        span = trace.get_current_span()
        span.set_attribute("session.id", session_id)
        span.set_attribute("request_id", request_id)
        synthetic = request.headers.get("x-telemetry-synthetic", "").lower() == "true"
        span.set_attribute("synthetic", synthetic)
        token = bind_request_context(
            request_id=request_id,
            session_id=session_id,
            synthetic=synthetic,
            http_method=request.method,
        )
        response = None
        error_tracking_token = begin_error_tracking_request()
        try:
            response = await call_next(request)
        except Exception:
            log_request_exception(logger, request=request, started_at=started_at)
            raise
        finally:
            if response is not None:
                _apply_html_preload_cache_headers(request, response)
                response.headers["X-Request-ID"] = request_id
                log_request(
                    logger,
                    request=request,
                    status_code=response.status_code,
                    started_at=started_at,
                )
            reset_request_context(token)
            clear_request_context()
            end_error_tracking_request(error_tracking_token)
        return response


def _install_http_exception_handler(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        fields = sorted(
            {
                ".".join(
                    str(part)
                    if isinstance(part, str)
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part)
                    else "[]"
                    for part in error["loc"]
                )
                for error in errors
            }
        )
        logger.warning(
            "http.validation.failed",
            extra={
                "status_code": 422,
                "http_method": request.method,
                "route_template": getattr(
                    request.scope.get("route"), "path", "unmatched"
                ),
                "validation_fields": ",".join(fields)[:1000],
                "validation_codes": ",".join(
                    sorted({error["type"] for error in errors})
                )[:1000],
                "validation_issue_count": len(errors),
            },
        )
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException):
        if exc.status_code == 401 and _is_web_navigation_request(request):
            if request.headers.get("HX-Request") == "true":
                return Response(status_code=200, headers={"HX-Redirect": "/login"})
            return RedirectResponse(url="/login", status_code=303)
        return await http_exception_handler(request, exc)


def _include_routers(app: FastAPI) -> None:
    app.include_router(system_router)
    app.include_router(internal_router)
    app.include_router(media_router)
    app.include_router(api_router)
    app.include_router(web_router)


def _apply_html_preload_cache_headers(request: Request, response) -> None:
    if request.method != "GET" or response.status_code != 200:
        return
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("text/html"):
        return
    if response.headers.get("Cache-Control"):
        _merge_vary_headers(response, "Cookie", "HX-Boosted", "HX-Request")
        return
    response.headers["Cache-Control"] = "private, no-cache"
    _merge_vary_headers(response, "Cookie", "HX-Boosted", "HX-Request")


def _merge_vary_headers(response, *values: str) -> None:
    existing = response.headers.get("Vary", "")
    merged = {value.strip() for value in existing.split(",") if value.strip()}
    merged.update(values)
    response.headers["Vary"] = ", ".join(sorted(merged))


def _is_web_navigation_request(request: Request) -> bool:
    path = request.url.path
    return not path.startswith("/api/")


def _requires_csrf_validation(request: Request) -> bool:
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if request.url.path.startswith("/api/"):
        return False
    if request.url.path in {"/login/app", "/login/app/code", "/login/demo"}:
        return True
    session_cookie_name = request.app.state.container.settings.session_cookie_name
    return bool(request.cookies.get(session_cookie_name))


async def _load_submitted_csrf_token(request: Request) -> str | None:
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if header_token:
        return header_token
    try:
        body = await request.body()
    except Exception:
        return None
    _restore_request_body(request, body)
    try:
        form = await request.form()
    except Exception:
        return None
    value = form.get(CSRF_FIELD_NAME)
    return value if isinstance(value, str) else None


def _restore_request_body(request: Request, body: bytes) -> None:
    sent = False

    def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive  # type: ignore[attr-defined]


def _install_security_headers(app: FastAPI) -> None:
    app.add_middleware(SecurityHeadersMiddleware)
