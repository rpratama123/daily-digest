from datetime import UTC, date, datetime
from pathlib import Path

from app.collectors.backups import collect_backups, format_transfer_size, inspect_backup
from app.models import BackupStatus, Status


def test_missing_file_is_structured(tmp_path: Path) -> None:
    result = inspect_backup(tmp_path / "missing.log", date(2026, 9, 5))
    assert result.status == BackupStatus.MISSING
    assert result.reason == "log_missing"


def test_collector_aggregates_independent_logs(tmp_path: Path) -> None:
    (tmp_path / "ok.log").write_text(
        "2026/09/05 start\nThere was nothing to transfer\n", encoding="utf-8"
    )
    now = datetime(2026, 9, 6, tzinfo=UTC)
    result = collect_backups(
        tmp_path,
        ["ok.log", "missing.log"],
        date(2026, 9, 5),
        now,
        now,
        now,
    )
    assert result.status == Status.FAILED
    assert result.data["ok_count"] == 1
    assert result.data["total_count"] == 2


def test_run_without_completion_evidence_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "run.log"
    path.write_text("2026/09/05 NOTICE: started\nChecks: 1 / 1\n", encoding="utf-8")
    result = inspect_backup(path, date(2026, 9, 5))
    assert result.status == BackupStatus.INCOMPLETE
    assert result.reason == "completion_not_found"


def test_successful_transfer_sizes_are_aggregated(tmp_path: Path) -> None:
    summaries = {
        "one.log": "Transferred: 1 MiB / 1 MiB, 100%\n",
        "two.log": "Transferred: 512 KiB / 512 KiB, 100%\n",
        "empty.log": "Transferred: 0 B / 0 B, -, 0 B/s, ETA -\n",
    }
    for name, summary in summaries.items():
        (tmp_path / name).write_text(f"2026/09/05 start\n{summary}", encoding="utf-8")
    now = datetime(2026, 9, 6, tzinfo=UTC)
    result = collect_backups(tmp_path, list(summaries), date(2026, 9, 5), now, now, now)
    assert result.status == Status.OK
    assert result.data["transferred_bytes"] == "1572864"
    assert result.data["transferred_total"] == "1.5 MiB"


def test_transfer_size_formatting_is_concise() -> None:
    from decimal import Decimal

    assert format_transfer_size(Decimal(0)) == "0 B"
    assert format_transfer_size(Decimal(42_407_000)) == "40.44 MiB"
