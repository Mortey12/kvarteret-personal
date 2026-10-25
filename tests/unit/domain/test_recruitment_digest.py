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
