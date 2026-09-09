import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.collectors.codex import CodexCollector
from app.models import Status
from scripts.codex_quota_export import ExportError, fetch_rate_limits, sanitized_export

FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "latest.json"
NOW = datetime(2026, 9, 9, 10, 10, tzinfo=UTC)


def test_exporter_extracts_only_sanitized_weekly_fields() -> None:
    raw = {
        "account": "must-not-export",
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 59,
                    "windowDurationMins": 300,
                    "resetsAt": 1788956154,
                },
                "secondary": {
                    "usedPercent": 50,
                    "windowDurationMins": 10080,
                    "resetsAt": 1789448432,
                },
            }
        },
    }
    exported = sanitized_export(raw, NOW)
    assert exported["weekly"]["remaining_percent"] == "50"
    assert "account" not in exported
    assert "primary" not in json.dumps(exported)


def test_collector_reads_fresh_weekly_quota(tmp_path: Path) -> None:
    export = tmp_path / "latest.json"
    export.write_bytes(FIXTURE.read_bytes())
    result = CodexCollector(export, "Asia/Jakarta", enabled=True).collect(NOW)
    assert result.status == Status.OK
    assert result.data["remaining_percent"] == "50"
    assert result.data["window_minutes"] == 10080
    assert result.data["reset_date"] == "2026-09-15"


def test_missing_stale_and_malformed_exports_are_isolated(tmp_path: Path) -> None:
    export = tmp_path / "latest.json"
    collector = CodexCollector(export, "Asia/Jakarta", enabled=True)
    assert collector.collect(NOW).error_code == "codex_export_missing"

    export.write_bytes(FIXTURE.read_bytes())
    stale = collector.collect(NOW + timedelta(hours=1))
    assert stale.status == Status.STALE
    assert stale.error_code == "codex_export_stale"

    export.write_text("not-json", encoding="utf-8")
    assert collector.collect(NOW).status == Status.UNAVAILABLE


def test_disabled_collector_does_not_read_file(tmp_path: Path) -> None:
    result = CodexCollector(tmp_path / "missing.json", "Asia/Jakarta").collect(NOW)
    assert result.status == Status.DISABLED


def test_exporter_rejects_non_weekly_secondary_window() -> None:
    raw = {
        "rateLimits": {
            "limitId": "codex",
            "secondary": {
                "usedPercent": 50,
                "windowDurationMins": 300,
                "resetsAt": 1789448432,
            },
        }
    }
    with pytest.raises(ExportError, match="unexpected_weekly_window"):
        sanitized_export(raw, NOW)


def test_exporter_uses_configured_codex_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def missing_binary(command: list[str], **_kwargs) -> None:
        calls.append(command)
        raise FileNotFoundError

    monkeypatch.setenv("CODEX_BINARY", "/config/bin/codex")
    monkeypatch.setattr(subprocess, "Popen", missing_binary)
    with pytest.raises(FileNotFoundError):
        fetch_rate_limits()
    assert calls == [["/config/bin/codex", "app-server", "--listen", "stdio://"]]
