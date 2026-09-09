from datetime import UTC, date, datetime
from pathlib import Path

from app.models import CollectorResult, DeliveryState, DigestReport, Status
from app.storage import Storage


def make_report() -> DigestReport:
    return DigestReport(
        delivery_date=date(2026, 9, 9),
        timezone="Asia/Jakarta",
        report_start=datetime(2026, 9, 7, 17, tzinfo=UTC),
        report_end=datetime(2026, 9, 8, 17, tzinfo=UTC),
        collected_at=datetime(2026, 9, 9, tzinfo=UTC),
        collectors=[
            CollectorResult(
                name="example", status=Status.OK, observed_at=datetime(2026, 9, 9, tzinfo=UTC)
            )
        ],
    )


def test_digest_round_trip_and_delivery_idempotency(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")
    storage.initialize()
    report = make_report()
    digest_id = storage.save_digest(report, "hello")
    stored = storage.get_latest_digest(report.delivery_date, report.timezone)
    assert stored is not None
    assert stored[1] == report

    now = datetime(2026, 9, 9, tzinfo=UTC)
    first = storage.claim_delivery(
        digest_id, report.delivery_date, report.timezone, "telegram", "private-chat", now
    )
    second = storage.claim_delivery(
        digest_id, report.delivery_date, report.timezone, "telegram", "private-chat", now
    )
    assert first is not None
    assert second is None
    storage.set_delivery_state(first, DeliveryState.UNKNOWN, now)
    assert (
        storage.delivery_state(report.delivery_date, report.timezone, "telegram", "private-chat")
        == DeliveryState.UNKNOWN
    )
    assert storage.reconcile_unknown(report.delivery_date, report.timezone, DeliveryState.SENT)
    assert (
        storage.delivery_state(report.delivery_date, report.timezone, "telegram", "private-chat")
        == DeliveryState.SENT
    )


def test_definitive_failure_can_be_safely_reclaimed(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")
    storage.initialize()
    report = make_report()
    digest_id = storage.save_digest(report)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    delivery_id = storage.claim_delivery(
        digest_id, report.delivery_date, report.timezone, "telegram", "private-chat", now
    )
    assert delivery_id is not None
    storage.set_delivery_state(delivery_id, DeliveryState.FAILED, now)
    assert (
        storage.claim_delivery(
            digest_id, report.delivery_date, report.timezone, "telegram", "private-chat", now
        )
        == delivery_id
    )
