import pytest
from datetime import UTC, datetime, timedelta

from app.domain.recruitment_digest.service import DigestCandidate, classify

NOW = datetime(2026, 10, 25, 6, 0, tzinfo=UTC)


def _candidate(*, status="new", created=NOW - timedelta(hours=3), ends=None):
    return DigestCandidate(1, status, created, ends, (1,))


def test_new_applicant_appears_once_in_new_section():
    assert classify(_candidate(), NOW) == ["new"]
    assert classify(_candidate(created=NOW - timedelta(hours=30)), NOW) == []


def test_waiting_reminders_after_three_and_seven_days():
    assert classify(_candidate(created=NOW - timedelta(days=3, hours=2)), NOW) == ["waiting_3d"]
    assert classify(_candidate(created=NOW - timedelta(days=7, hours=2)), NOW) == ["waiting_7d"]


def test_waiting_reminders_skip_contacted_applicants():
    contacted = _candidate(status="contacted", created=NOW - timedelta(days=3, hours=2))
    assert classify(contacted, NOW) == []


def test_trial_seven_days_left_and_done():
    seven = _candidate(status="trial", created=NOW - timedelta(days=30), ends=NOW + timedelta(days=6, hours=20))
    done = _candidate(status="trial", created=NOW - timedelta(days=30), ends=NOW - timedelta(hours=5))
    assert classify(seven, NOW) == ["trial_7d"]
    assert classify(done, NOW) == ["trial_done"]
    assert classify(_candidate(status="trial", created=NOW - timedelta(days=30), ends=NOW + timedelta(days=3)), NOW) == []


def test_snapshot_lists_everyone_currently_waiting_or_on_trial():
    from app.domain.recruitment_digest.service import classify_snapshot

    old_waiting = _candidate(created=NOW - timedelta(days=40))
    active = _candidate(status="trial", ends=NOW + timedelta(days=3))
    done = _candidate(status="trial", ends=NOW - timedelta(days=2))
    contacted = _candidate(status="contacted")
    assert classify_snapshot(old_waiting, NOW) == ["waiting"]
    assert classify_snapshot(active, NOW) == ["trial_active"]
    assert classify_snapshot(done, NOW) == ["trial_done"]
    assert classify_snapshot(contacted, NOW) == ["contacted"]
    assert classify_snapshot(_candidate(status="volunteer"), NOW) == []


def test_previous_semester_wraps_the_year():
    from app.domain.recruitment_digest.service import _previous_semester

    assert _previous_semester(20262) == 20261
    assert _previous_semester(20261) == 20252


def test_long_sections_collapse_only_when_more_than_ten_would_be_hidden():
    from app.domain.recruitment_digest.service import MAX_HIDDEN_BEFORE_COLLAPSE, MAX_LISTED_PER_SECTION

    assert MAX_LISTED_PER_SECTION == 5
    assert MAX_HIDDEN_BEFORE_COLLAPSE == 10


@pytest.mark.asyncio
async def test_no_digest_is_queued_when_there_is_nothing_to_report(monkeypatch):
    from app.domain.recruitment_digest.service import RecruitmentDigestService

    class Outbox:
        queued: list = []

        async def enqueue(self, request):
            self.queued.append(request)

    service = RecruitmentDigestService(email_outbox=Outbox())

    async def nothing(self, now, *, snapshot):
        return {}, {}

    monkeypatch.setattr(RecruitmentDigestService, "_collect", nothing)

    assert await service.enqueue_daily_digests(now=NOW) == 0
    assert await service.enqueue_status_digests(now=NOW) == ([], [])
    assert Outbox.queued == []


@pytest.mark.asyncio
async def test_fuse_queues_nothing_when_an_implausible_number_of_digests_would_go_out(monkeypatch):
    from app.domain.recruitment_digest.service import MAX_DIGESTS_PER_RUN, RecruitmentDigestService

    class Outbox:
        queued: list = []

        async def enqueue(self, request):
            self.queued.append(request)

    async def flood(self, now, *, snapshot):
        return {(f"r{i}@example.com", 1): {"name": None, "sections": {"new": [i]}} for i in range(MAX_DIGESTS_PER_RUN + 1)}, {}

    monkeypatch.setattr(RecruitmentDigestService, "_collect", flood)

    assert await RecruitmentDigestService(email_outbox=Outbox()).enqueue_daily_digests(now=NOW) == 0
    assert Outbox.queued == []


@pytest.mark.parametrize(
    "context",
    [
        {"new": [7], "waiting_3d": [8]},
        {"new": [7], "_group_id": 1, "_name": None},
        {"waiting": [7], "_group_id": 1, "_name": "Kari", "_dry_run_for": "a@example.test"},
        {"_missing_groups": [{"group_id": 1, "name": "Bar", "applicants": 2}]},
    ],
)
def test_digest_context_from_any_released_producer_is_accepted(context):
    import json

    from app.domain.recruitment_digest.service import parse_digest_context

    assert parse_digest_context(json.dumps(context).encode()) == context


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"[1]", b'{"new": 5}', b'{"new": ["x"]}', b'{"_group_id": [1]}', b'{"other": [1]}'],
)
def test_unknown_digest_context_shape_is_rejected(raw):
    from app.domain.recruitment_digest.service import parse_digest_context

    assert parse_digest_context(raw) is None
