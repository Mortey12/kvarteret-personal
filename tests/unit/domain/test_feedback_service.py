import io
import json
import logging
from urllib import error as urllib_error

import pytest

from app.config import Settings
from app.db.rate_limit import InMemoryRateLimiter
from app.domain.feedback.service import (
    FeedbackDeliveryError,
    FeedbackRateLimitedError,
    FeedbackService,
    FeedbackSubmission,
    _create_linear_issue,
)


def test_create_linear_issue_lets_linear_assign_triage_state_and_includes_email(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    responses = [
        b'{"data":{"issueCreate":{"success":true,'
        b'"issue":{"id":"issue-id","identifier":"DAK-123","url":"https://linear.app/kvarteret/issue/DAK-123/test"}}}}',
    ]

    class FakeResponse:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return self.body

    def fake_urlopen(req, timeout: int):
        captured.append(
            {
                "timeout": timeout,
                "payload": json.loads(req.data.decode("utf-8")),
                "authorization": req.headers["Authorization"],
            }
        )
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr("app.domain.feedback.service.urllib_request.urlopen", fake_urlopen)

    _create_linear_issue(
        FeedbackSubmission(
            category="forslag",
            feedback_type=None,
            name="System User",
            email="admin.user@example.test",
            message="Legg til bedre sok.",
            page="/volunteers",
            platform="web",
            user_id=None,
            submitted_at="21.05.2026 15:20",
            source="personalplattformen",
        ),
        Settings(
            linear_api_key="lin_api_example",
            linear_team_id="team-id",
            linear_project_id_personal="project-id",
            linear_state_id_triage="triage-state-id",
        ),
    )

    issue_input = captured[0]["payload"]["variables"]["input"]
    assert issue_input["teamId"] == "team-id"
    assert issue_input["projectId"] == "project-id"
    assert "stateId" not in issue_input
    assert "E-post:** admin.user@example.test" in issue_input["description"]
    assert captured[0]["authorization"] == "lin_api_example"
    assert len(captured) == 1


def _settings_for_rate_limit_tests(**overrides) -> Settings:
    return Settings(
        linear_api_key="lin_api_example",
        linear_team_id="team-id",
        linear_project_id_internbevis="project-id",
        feedback_submission_limit=2,
        feedback_submission_window_seconds=600,
        **overrides,
    )


@pytest.mark.asyncio
async def test_submit_feedback_raises_after_limit_exceeded_for_same_source_key(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.domain.feedback.service._create_linear_issue", lambda *a, **k: None
    )
    service = FeedbackService(
        _settings_for_rate_limit_tests(), rate_limiter=InMemoryRateLimiter()
    )

    for _ in range(2):
        await service.submit_feedback(
            name=None,
            email=None,
            message="Hei",
            page="/home",
            source="internbevis-rn",
            source_key="1.2.3.4",
        )

    with pytest.raises(FeedbackRateLimitedError):
        await service.submit_feedback(
            name=None,
            email=None,
            message="Hei igjen",
            page="/home",
            source="internbevis-rn",
            source_key="1.2.3.4",
        )


@pytest.mark.asyncio
async def test_submit_feedback_rate_limit_is_scoped_per_source_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domain.feedback.service._create_linear_issue", lambda *a, **k: None
    )
    service = FeedbackService(
        _settings_for_rate_limit_tests(), rate_limiter=InMemoryRateLimiter()
    )

    for _ in range(2):
        await service.submit_feedback(
            name=None,
            email=None,
            message="Hei",
            page="/home",
            source="internbevis-rn",
            source_key="1.2.3.4",
        )

    # A different source key has its own counter and is unaffected.
    await service.submit_feedback(
        name=None,
        email=None,
        message="Hei fra en annen IP",
        page="/home",
        source="internbevis-rn",
        source_key="5.6.7.8",
    )


@pytest.mark.asyncio
async def test_submit_feedback_without_source_key_uses_a_shared_fallback_limit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.domain.feedback.service._create_linear_issue", lambda *a, **k: None
    )
    service = FeedbackService(
        _settings_for_rate_limit_tests(), rate_limiter=InMemoryRateLimiter()
    )

    for _ in range(2):
        await service.submit_feedback(
            name=None,
            email=None,
            message="Hei",
            page="/home",
            source="internbevis-rn",
            source_key=None,
        )

    with pytest.raises(FeedbackRateLimitedError):
        await service.submit_feedback(
            name=None,
            email=None,
            message="Hei igjen",
            page="/home",
            source="internbevis-rn",
            source_key=None,
        )


_SENTINEL_MESSAGE = "Sentinel feedback text"
_SENTINEL_EMAIL = "sentinel.person@example.test"


def _sentinel_submission() -> FeedbackSubmission:
    return FeedbackSubmission(
        category="ris",
        feedback_type=None,
        name="Sentinel Name",
        email=_SENTINEL_EMAIL,
        message=_SENTINEL_MESSAGE,
        page="/home",
        platform="ios",
        user_id=None,
        submitted_at="22.10.2026 12:46",
        source="internbevis-rn",
    )


def _linear_settings() -> Settings:
    return Settings(
        linear_api_key="lin_api_sentinel",
        linear_team_id="team-id",
        linear_project_id_internbevis="project-id",
    )


_LINEAR_ERROR_BODY = json.dumps(
    {
        "data": None,
        "errors": [
            {
                "message": f"Invalid input: {_SENTINEL_MESSAGE} {_SENTINEL_EMAIL}",
                "path": ["issueCreate"],
                "extensions": {
                    "code": "INPUT_ERROR",
                    "type": "invalid input",
                    "userError": True,
                    "userPresentableMessage": _SENTINEL_EMAIL,
                },
            },
            {
                "message": "Entity not found",
                "path": ["issueCreate", 0, "project"],
                "extensions": {"code": f"bad code {_SENTINEL_EMAIL}"},
            },
        ],
    }
).encode("utf-8")


def _failure_record(caplog, event: str):
    records = [r for r in caplog.records if getattr(r, "event", None) == event]
    assert len(records) == 1
    return records[0]


def _assert_no_sentinels(record) -> None:
    from app.observability import JsonLogFormatter

    exported = JsonLogFormatter().format(record) + json.dumps(record.event_data)
    for sentinel in (_SENTINEL_MESSAGE, _SENTINEL_EMAIL, "lin_api_sentinel", "project-id"):
        assert sentinel not in exported


def test_graphql_rejection_logs_safe_linear_error_fields(monkeypatch, caplog) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return _LINEAR_ERROR_BODY

    monkeypatch.setattr(
        "app.domain.feedback.service.urllib_request.urlopen",
        lambda req, timeout: FakeResponse(),
    )

    with caplog.at_level(logging.ERROR), pytest.raises(FeedbackDeliveryError):
        _create_linear_issue(_sentinel_submission(), _linear_settings())

    record = _failure_record(caplog, "feedback.issue.graphql_failed")
    assert record.event_data == {
        "feedback_source": "internbevis-rn",
        "linear_operation": "issueCreate",
        "linear_error_count": 2,
        "linear_error_codes": "INPUT_ERROR,other",
        "linear_error_types": "invalid_input",
        "linear_error_paths": "issueCreate,issueCreate.project",
        "linear_user_error": True,
    }
    _assert_no_sentinels(record)


def test_http_rejection_logs_status_and_safe_linear_error_fields(
    monkeypatch, caplog
) -> None:
    def fake_urlopen(req, timeout: int):
        raise urllib_error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(_LINEAR_ERROR_BODY)
        )

    monkeypatch.setattr(
        "app.domain.feedback.service.urllib_request.urlopen", fake_urlopen
    )

    with caplog.at_level(logging.ERROR), pytest.raises(FeedbackDeliveryError):
        _create_linear_issue(_sentinel_submission(), _linear_settings())

    record = _failure_record(caplog, "feedback.issue.http_failed")
    assert record.event_data["dependency_status_code"] == 400
    assert record.event_data["linear_error_codes"] == "INPUT_ERROR,other"
    assert record.event_data["feedback_source"] == "internbevis-rn"
    _assert_no_sentinels(record)


def test_http_rejection_without_json_body_keeps_status(monkeypatch, caplog) -> None:
    def fake_urlopen(req, timeout: int):
        raise urllib_error.HTTPError(
            req.full_url, 502, "Bad Gateway", {}, io.BytesIO(b"<html>")
        )

    monkeypatch.setattr(
        "app.domain.feedback.service.urllib_request.urlopen", fake_urlopen
    )

    with caplog.at_level(logging.ERROR), pytest.raises(FeedbackDeliveryError):
        _create_linear_issue(_sentinel_submission(), _linear_settings())

    record = _failure_record(caplog, "feedback.issue.http_failed")
    assert record.event_data == {
        "feedback_source": "internbevis-rn",
        "linear_operation": "issueCreate",
        "dependency_status_code": 502,
    }
