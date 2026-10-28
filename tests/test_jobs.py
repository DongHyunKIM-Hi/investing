from datetime import UTC, datetime, timedelta

from app.models import JobRun
from app.services.jobs import is_stale


def test_is_stale():
    now = datetime(2026, 10, 29, 9, tzinfo=UTC)
    assert is_stale(None, now)
    fresh = JobRun(name="recompute", ran_at=now - timedelta(days=2, hours=23), ok=True)
    old = JobRun(name="recompute", ran_at=now - timedelta(days=3, minutes=1), ok=True)
    assert not is_stale(fresh, now)
    assert is_stale(old, now)
