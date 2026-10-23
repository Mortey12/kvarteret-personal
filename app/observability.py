from __future__ import annotations

import json
import hashlib
import hmac
import logging
import re
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request
from itsdangerous import BadSignature, URLSafeSerializer
from opentelemetry import trace

from app.auth.models import AuthenticatedUser
from app.config import Settings
from app.exception_diagnostics import exception_diagnostics

_request_context: ContextVar[dict[str, Any]] = ContextVar("request_context", default={})

# The root logger stays at settings.log_level, so application logs still emit at INFO by default.
# Only these specific third-party loggers are overridden to WARNING to reduce Vercel noise.
_NOISY_LOGGER_LEVELS: dict[str, int] = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "azure": logging.WARNING,
    "azure.core": logging.WARNING,
    "azure.storage": logging.WARNING,
    "azure.core.pipeline.policies.http_logging_policy": logging.WARNING,
    "opentelemetry": logging.WARNING,
    "uvicorn.access": logging.WARNING,
}

_COMMON_FIELDS = frozenset(
    {
        "service",
        "environment",
        "request_id",
        "session_id",
        "synthetic",
        "trace_id",
        "span_id",
        "registration_id",
        "origin_trace_id",
        "volunteer_id",
        "email_delivery_id",
        "template_key",
        "status",
        "status_code",
        "outcome",
        "failure_stage",
        "error_category",
        "dependency_status_code",
        "error_file",
        "error_function",
        "error_line",
        "error_chain",
        "db_sqlstate",
        "retryable",
        "delivery_uncertain",
        "smtp_status_class",
        "http_status_class",
        "attempt_no",
        "duration_ms",
        "http_method",
        "route_template",
        "user_account_id",
        "count",
        "claimed_count",
        "sent_count",
        "failed_count",
        "retrying_count",
        "interrupted_count",
    }
)

# Every event-specific field is declared here. Callers cannot add arbitrary data.
_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "auth.login.failed": frozenset({"reason"}),
    "auth.login.succeeded": frozenset({"role"}),
    "volunteer.prospect.auth.rejected": frozenset({"reason"}),
    "http.validation.failed": frozenset(
        {"validation_fields", "validation_codes", "validation_issue_count"}
    ),
    "form.submission.repeated_failure": frozenset(
        {
            "form_id",
            "attempt_count",
            "validation_fields",
            "validation_codes",
            "error_source",
        }
    ),
    "admin.activity": frozenset(
        {
            "action",
            "subject_type",
            "subject_id",
            "admin_user_account_id",
            "admin_role",
            "role",
            "delivery",
            "setup_url_created",
            "enabled",
            "impersonated_user_account_id",
        }
    ),
    "app.operation.timing": frozenset(
        {"operation", "limit", "semester_code", "query_present"}
    ),
    "email.delivery": frozenset({"lease_owner"}),
    "mobile_card.access_code.sent": frozenset({"subject_type", "subject_id"}),
    "mobile_card.session.created": frozenset({"subject_type", "subject_id"}),
    "mobile_card.session.renewed": frozenset(
        {
            "age_seconds",
            "is_review",
            "remaining_seconds",
            "ttl_seconds",
            "renewal_threshold_seconds",
        }
    ),
    "mobile_card.session.invalid": frozenset({"reason"}),
    "mobile_card.client_session_logout": frozenset(
        {
            "event_name",
            "platform",
            "app_version",
            "auth_error_code",
            "auth_error_status",
            "had_cached_user",
            "had_login_marker",
            "had_stored_credentials",
        }
    ),
    "mobile_card.client_diagnostic": frozenset(
        {
            "event_name",
            "event_id",
            "operation_id",
            "attempt_id",
            "source",
            "occurred_at",
            "platform",
            "app_version",
            "runtime_version",
            "update_channel",
            "update_id",
            "auth_error_code",
            "auth_error_status",
            "had_cached_user",
            "had_login_marker",
            "had_stored_credentials",
        }
    ),
    "mobile_card.logout.succeeded": frozenset(
        {
            "event_name",
            "event_id",
            "operation_id",
            "attempt_id",
            "source",
            "occurred_at",
            "platform",
            "app_version",
            "runtime_version",
            "update_channel",
            "update_id",
        }
    ),
    "feedback.issue.created": frozenset({"feedback_source", "issue_identifier"}),
    "feedback.issue.graphql_failed": frozenset(
        {
            "operation",
            "graphql_error_count",
            "graphql_error_codes",
            "graphql_error_paths",
        }
    ),
    "feedback.issue.http_failed": frozenset({"http_status"}),
    "volunteer.prospect.conflict": frozenset({"conflict_type"}),
    "volunteer.prospect.validation_failed": frozenset(
        {"validation_codes", "validation_fields", "validation_issue_count"}
    ),
    "web.client_error": frozenset(
        {
            "error_type",
            "error_text",
            "error_source",
            "form_id",
            "attempt_count",
            "validation_fields",
            "validation_codes",
        }
    ),
}

# Client payload names remain compatible; exported bodies describe domain outcomes.
MOBILE_DIAGNOSTIC_EVENTS = {
    "logout_succeeded": "mobile_card.logout.succeeded",
    "logout_failed": "mobile_card.logout.failed",
    "session_invalidated": "mobile_card.session.invalidated",
    "credentials_missing_after_login": "mobile_card.credentials.missing_after_login",
    "response_invalid": "mobile_card.response.invalid",
    "session_token_persist_failed": "mobile_card.session.persistence.failed",
    "cache_fallback_started": "mobile_card.cache.fallback.started",
    "cache_fallback_recovered": "mobile_card.cache.fallback.recovered",
}
for _diagnostic_event in MOBILE_DIAGNOSTIC_EVENTS.values():
    _EVENT_FIELDS[_diagnostic_event] = _EVENT_FIELDS["mobile_card.client_diagnostic"]


# INFO is an explicit contract: adding a named visit/read event cannot increase
# ingestion by accident. Warnings and errors remain unconditional.
DOMAIN_OUTCOME_EVENTS = frozenset(
    {
        "auth.login.succeeded",
        "volunteer.lifecycle",
        "admin.activity",
        "email.delivery",
        "mobile_card.access_code.sent",
        "mobile_card.session.created",
        "mobile_card.logout.succeeded",
        "feedback.issue.created",
    }
)


def diagnostic_session_id(request: Request, settings: Settings) -> str:
    """Correlate browser requests without exporting a credential or adding a cookie."""
    supplied = request.headers.get("x-session-id", "")
    valid_supplied = (
        bool(re.fullmatch(r"[a-f0-9]{32}", supplied)) and supplied != "0" * 32
    )
    # Mobile clients carry their own diagnostic context; browser CSRF cookies
    # must not split API requests from the client's queued logout diagnostics.
    if valid_supplied and request.scope.get("path", "").startswith(
        "/api/v1/mobile-card/"
    ):
        return supplied
    cookie = request.cookies.get("kvarteret_csrf")
    valid_cookie = False
    if cookie:
        try:
            payload = URLSafeSerializer(
                settings.app_secret_key, salt="kvarteret-csrf"
            ).loads(cookie)
            valid_cookie = (
                isinstance(payload, dict)
                and isinstance(payload.get("nonce"), str)
                and bool(payload["nonce"])
            )
        except BadSignature:
            pass
    if valid_cookie:
        return hmac.new(
            settings.app_secret_key.encode(),
            ("logging-session:" + cookie).encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
    # Cookie-less callers may supply a random diagnostic ID. It grants no access.
    if valid_supplied:
        return supplied
    return uuid4().hex


_FORBIDDEN_KEY_PARTS = (
    "authorization",
    "cookie",
    "email",
    "password",
    "phone",
    "secret",
    "token",
    "username",
    "body",
    "message",
    "url",
    "client_ip",
    "user_agent",
)
_EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_QUERY_PATTERN = re.compile(r"\?[^\s\"']+")
_TOKEN_PATH_PATTERN = re.compile(r"(/(?:apply|set-password)/)[^/?#\s]+", re.I)


def current_trace_fields() -> dict[str, str]:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }


def current_trace_id() -> str | None:
    return current_trace_fields().get("trace_id")


def _is_safe_scalar(value: object) -> bool:
    return value is None or isinstance(value, (bool, int, float, str, UUID))


def _allowed_fields(event: str) -> frozenset[str]:
    return _COMMON_FIELDS | _EVENT_FIELDS.get(event, frozenset())


def sanitize_fields(event: str, values: Mapping[str, object]) -> dict[str, object]:
    allowed = _allowed_fields(event)
    sanitized: dict[str, object] = {}
    for key, value in values.items():
        normalized_key = key.strip()
        if normalized_key not in allowed or not _is_safe_scalar(value):
            continue
        # These explicitly declared fields contain an ID/boolean, not an email
        # address or URL. Keep them usable for domain-event correlation.
        if normalized_key == "setup_url_created" and not isinstance(value, bool):
            continue
        if normalized_key not in {"email_delivery_id", "setup_url_created"} and any(
            part in normalized_key.lower() for part in _FORBIDDEN_KEY_PARTS
        ):
            continue
        sanitized[normalized_key] = _sanitize_scalar(value)
    return sanitized


def _sanitize_scalar(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        return value
    redacted = _EMAIL_PATTERN.sub("[redacted-email]", value)
    redacted = _BEARER_PATTERN.sub("Bearer [redacted]", redacted)
    redacted = _QUERY_PATTERN.sub("?[redacted]", redacted)
    return _TOKEN_PATH_PATTERN.sub(r"\1[redacted]", redacted)


class EventLoggerAdapter(logging.LoggerAdapter):
    """Standard logging methods with safe domain fields and captured context."""

    def process(self, msg, kwargs):
        extra = dict(kwargs.get("extra") or {})
        event = extra.pop("event", None)
        if event is None:
            event = (
                msg
                if isinstance(msg, str)
                and re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", msg)
                else "log.message"
            )
        fields = {**extra.pop("event_data", {}), **extra}
        context = dict(_request_context.get({}))
        request = context.pop("_request", None)
        if request is not None:
            context["route_template"] = _route_template(request)
        kwargs["extra"] = {
            "event": event,
            "event_data": sanitize_fields(
                event, {**fields, **context, **current_trace_fields()}
            ),
            "_otel_context": trace.set_span_in_context(trace.get_current_span()),
        }
        kwargs["stacklevel"] = kwargs.get("stacklevel", 1) + 1
        return msg, kwargs

    def log(self, level, msg, *args, after_commit=False, **kwargs):
        if not self.isEnabledFor(level):
            return
        msg, kwargs = self.process(msg, kwargs)
        request = _request_context.get({}).get("_request")
        if (
            request is not None
            and level >= logging.ERROR
            and kwargs["extra"]["event"] not in {"http.request.failed", "log.message"}
        ):
            request.state.domain_error_logged = True
        if not after_commit:
            self.logger.log(level, msg, *args, **kwargs)
            return
        # Build the record now so commits retain the original caller and trace.
        filename, lineno, func, stack = self.logger.findCaller(
            kwargs.get("stack_info", False), kwargs.get("stacklevel", 2)
        )
        exc_info = kwargs.get("exc_info")
        if isinstance(exc_info, BaseException):
            exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
        elif exc_info and not isinstance(exc_info, tuple):
            exc_info = sys.exc_info()
        record = self.logger.makeRecord(
            self.logger.name,
            level,
            filename,
            lineno,
            msg,
            args,
            exc_info,
            func,
            kwargs["extra"],
            stack,
        )
        _log_after_commit(self.logger, record)


def get_logger(name: str) -> EventLoggerAdapter:
    return EventLoggerAdapter(logging.getLogger(name), {})


def _log_after_commit(logger: logging.Logger, record: logging.LogRecord) -> None:
    """Publish a captured record only when its transaction succeeds."""
    from sqlalchemy import event as sqlalchemy_event

    from app.db.session import current_session

    session = current_session()
    if session is None or not session.in_transaction():
        logger.handle(record)
        return
    sync_session = session.sync_session
    key = "observability.committed_events"
    if key not in sync_session.info:
        sync_session.info[key] = []

        def committed(session):
            if session.in_nested_transaction():
                return
            pending, session.info[key] = session.info[key], []
            for event_logger, record, transaction in pending:
                event_logger.handle(record)

        def rolled_back(session, previous_transaction):
            def belongs_to_rollback(item):
                transaction = item[-1]
                while transaction is not None:
                    if transaction is previous_transaction:
                        return True
                    transaction = transaction.parent
                return False

            session.info[key] = [
                item for item in session.info[key] if not belongs_to_rollback(item)
            ]

        def ended(session, transaction):
            # Session.close() ends an uncommitted transaction without invoking
            # after_rollback. Never carry its events into a reused session.
            if transaction.parent is None:
                session.info[key].clear()

        sqlalchemy_event.listen(sync_session, "after_commit", committed)
        sqlalchemy_event.listen(sync_session, "after_soft_rollback", rolled_back)
        sqlalchemy_event.listen(sync_session, "after_transaction_end", ended)
    sync_session.info[key].append(
        (
            logger,
            record,
            sync_session.get_nested_transaction() or sync_session.get_transaction(),
        )
    )


_ADMIN_DIAGNOSTIC_ACTIONS = frozenset(
    {
        "volunteer_application.list",
        "volunteer_application.view",
        "email_delivery.list",
        "email_delivery.view",
        "admin_account.list",
        "admin_account.view_detail",
        "volunteer.view_detail",
        "search.volunteers",
        "group.preview_semester_transfer",
        "spotify.now_playing.view",
        "spotify.oauth.login.start",
    }
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = str(getattr(record, "event", "log.message"))
        payload: dict[str, Any] = {
            "schema_version": 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
            # Free-form log messages can contain names, response bodies, or other
            # identifiers that pattern redaction cannot reliably recognize.
            # Export the stable event name and require useful diagnostics to use
            # the allowlisted structured fields below.
            "message": event,
        }
        payload.update(sanitize_fields(event, _request_context.get({})))
        event_data = getattr(record, "event_data", None)
        if isinstance(event_data, dict):
            payload.update(sanitize_fields(event, event_data))
        payload.update(current_trace_fields())
        if record.exc_info:
            payload.update(
                sanitize_fields(event, exception_diagnostics(record.exc_info[1]))
            )
        action = payload.get("action") if event == "admin.activity" else None
        payload["domain"] = str(action or event).split(".", 1)[0]
        return json.dumps(payload, default=str, ensure_ascii=True)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    for logger_name, level in _NOISY_LOGGER_LEVELS.items():
        logging.getLogger(logger_name).setLevel(level)


def bind_request_context(**values: Any):
    current = dict(_request_context.get({}))
    current.update(sanitize_fields("http.request.completed", values))
    return _request_context.set(current)


def reset_request_context(token) -> None:
    _request_context.reset(token)


def clear_request_context() -> None:
    _request_context.set({})


def build_request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "")
    return supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", supplied) else uuid4().hex


def client_ip_from_request(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else None


def request_context_for_user(user: AuthenticatedUser | None) -> dict[str, Any]:
    if user is None:
        return {}
    return {"user_account_id": user.user_account_id}


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return "unmatched"


def log_request(
    logger: EventLoggerAdapter, *, request: Request, status_code: int, started_at: float
) -> None:
    logger.log(
        logging.ERROR
        if status_code >= 500
        else logging.WARNING
        if status_code >= 400
        else logging.DEBUG,
        "http.request.failed" if status_code >= 400 else "http.request.completed",
        stacklevel=2,
        extra={
            "status_code": status_code,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "http_method": request.method,
            "route_template": _route_template(request),
            "outcome": "failed" if status_code >= 400 else "succeeded",
        },
    )


def log_request_exception(
    logger: EventLoggerAdapter, *, request: Request, started_at: float
) -> None:
    logger.exception(
        "http.request.failed",
        stacklevel=2,
        extra={
            "event": "http.request.failed",
            "event_data": {
                "status_code": 500,
                "outcome": "failed",
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                "http_method": request.method,
                "route_template": _route_template(request),
            },
        },
    )


def log_admin_activity(
    *,
    request: Request,
    user: AuthenticatedUser,
    action: str,
    outcome: str = "success",
    subject_type: str | None = None,
    subject_id: str | int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    safe_details = dict(details or {})
    if "setup_url" in safe_details:
        safe_details["setup_url_created"] = bool(safe_details.pop("setup_url"))
    # Reads and OAuth initiation are diagnostics, not business changes.
    diagnostic = action in _ADMIN_DIAGNOSTIC_ACTIONS
    get_logger("app.audit").log(
        logging.WARNING
        if outcome != "success"
        else logging.DEBUG
        if diagnostic
        else logging.INFO,
        "admin.activity",
        stacklevel=2,
        after_commit=not diagnostic and outcome == "success",
        extra={
            "action": action,
            "outcome": outcome,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "route_template": _route_template(request),
            "admin_user_account_id": user.user_account_id,
            "admin_role": user.role.value,
            **safe_details,
        },
    )


def redact_query_string(request: Request) -> str:
    # Kept for call-site compatibility. Query values are never exported.
    return "[redacted]" if request.query_params else ""


def log_operation_timing(
    logger: EventLoggerAdapter,
    *,
    operation: str,
    started_at: float,
    details: dict[str, Any] | None = None,
) -> None:
    logger.debug(
        "app.operation.timing",
        stacklevel=2,
        extra={
            "operation": operation,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            **(details or {}),
        },
    )


_tracer = trace.get_tracer("kvarteret-personal")


@contextmanager
def with_named_span(name: str, attributes: Mapping[str, object] | None = None):
    """Run the wrapped block inside a named business-domain span.

    The span records ERROR status when the block raises. When telemetry is
    disabled the tracer yields a non-recording span and all calls are no-ops,
    so this helper is safe to use unconditionally.
    """
    with _tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as span:
        for key, value in (attributes or {}).items():
            span.set_attribute(key, value)
        try:
            yield span
        except BaseException:
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            raise
