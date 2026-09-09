from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.scheduler import heartbeat_is_healthy, should_catch_up, write_heartbeat


def test_catch_up_only_at_or_after_digest_hour() -> None:
    assert not should_catch_up(datetime(2026, 9, 9, 6, 59, tzinfo=UTC), 7)
    assert should_catch_up(datetime(2026, 9, 9, 7, 0, tzinfo=UTC), 7)


def test_heartbeat_health(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    now = datetime(2026, 9, 9, tzinfo=UTC)
    write_heartbeat(path, now)
    assert heartbeat_is_healthy(path, now + timedelta(minutes=4))
    assert not heartbeat_is_healthy(path, now + timedelta(minutes=6))
