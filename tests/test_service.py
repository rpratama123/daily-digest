import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.deliverers.telegram import TelegramUnknownError
from app.models import DeliveryState, ProviderSnapshot, Status
from app.renderers.telegram import render_telegram
from app.service import DigestService, fixture_report
from app.storage import Storage


def settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "state_directory": tmp_path,
        "home_assistant": {"enabled": False},
        "backups": {"enabled": False},
        "expenses": {
            "enabled": False,
            "username_file": tmp_path / "must-not-be-read",
            "password_file": tmp_path / "must-not-be-read-either",
        },
        "openrouter": {"enabled": False},
        "deepseek": {"enabled": False},
        "vellum": {"enabled": False},
        "codex": {"enabled": False},
        "telegram": {"enabled": False},
    }
    values.update(overrides)
    return Settings(**values)


def test_disabled_collectors_have_no_network_or_secret_side_effects(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")
    storage.initialize()
    service = DigestService(settings(tmp_path), storage)
    report = service.collect(date(2026, 9, 9), datetime(2026, 9, 9, tzinfo=UTC))
    assert all(item.status == Status.DISABLED for item in report.collectors)
    assert storage.get_latest_digest(date(2026, 9, 9), "Asia/Jakarta") is not None


def test_backup_failure_does_not_suppress_other_sections(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")
    storage.initialize()
    service = DigestService(
        settings(
            tmp_path,
            backups={"enabled": True, "directory": tmp_path, "logs": ["missing.log"]},
        ),
        storage,
    )
    report = service.collect(date(2026, 9, 9), datetime(2026, 9, 9, tzinfo=UTC))
    assert report.by_name("backups").status == Status.FAILED
    assert report.by_name("expenses").status == Status.DISABLED


def test_enabled_codex_export_is_collected_without_credentials(tmp_path: Path) -> None:
    export = tmp_path / "latest.json"
    fixture = Path(__file__).parent / "fixtures" / "codex" / "latest.json"
    export.write_bytes(fixture.read_bytes())
    storage = Storage(tmp_path / "state.sqlite3")
    storage.initialize()
    service = DigestService(
        settings(tmp_path, codex={"enabled": True, "export_file": export}), storage
    )
    report = service.collect(date(2026, 9, 9), datetime(2026, 9, 9, 10, 10, tzinfo=UTC))
    codex = report.by_name("codex")
    assert codex is not None
    assert codex.status == Status.OK
    assert codex.data["remaining_percent"] == "50"


def test_cumulative_snapshot_pair_is_read_from_exact_boundaries(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "state.sqlite3")
    storage.initialize()
    now = datetime(2026, 9, 9, tzinfo=UTC)
    for boundary, value in ((date(2026, 9, 8), "100.25"), (date(2026, 9, 9), "109.75")):
        storage.save_snapshot(
            ProviderSnapshot(
                provider="home_assistant",
                boundary_date=boundary,
                observed_at=now,
                kind="cumulative_energy",
                payload={"value": value, "unit": "kWh"},
                canonical=True,
            )
        )
    service = DigestService(settings(tmp_path), storage)
    from app.clock import DigestContext

    assert service._cumulative_energy_pair(DigestContext.create(date(2026, 9, 9))) == (
        Decimal("100.25"),
        Decimal("109.75"),
    )


def test_ambiguous_telegram_send_is_held_as_unknown(tmp_path: Path) -> None:
    token_file = tmp_path / "telegram-token"
    chat_file = tmp_path / "telegram-chat"
    token_file.write_text("fake-token", encoding="utf-8")
    chat_file.write_text("12345", encoding="utf-8")
    configured = settings(
        tmp_path,
        telegram={
            "enabled": True,
            "bot_token_file": token_file,
            "chat_id_file": chat_file,
        },
    )
    storage = Storage(tmp_path / "state.sqlite3")
    storage.initialize()
    report = fixture_report(date(2026, 9, 9))
    storage.save_digest(report, render_telegram(report))
    service = DigestService(configured, storage)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/getChat":
            return httpx.Response(200, json={"ok": True, "result": {"type": "private"}})
        raise httpx.ReadTimeout("unknown", request=request)

    service._http_client = lambda base_url: httpx.Client(  # type: ignore[method-assign]
        base_url="https://telegram.test", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(TelegramUnknownError):
        service.send(report.delivery_date)

    destination = hashlib.sha256(b"12345").hexdigest()[:16]
    assert (
        storage.delivery_state(report.delivery_date, report.timezone, "telegram", destination)
        == DeliveryState.UNKNOWN
    )
