from datetime import date

from app.models import Status
from app.renderers.telegram import format_idr, render_telegram
from app.service import fixture_report


def test_fixture_digest_is_concise_and_correctly_labeled() -> None:
    message = render_telegram(fixture_report(date(2026, 9, 6)))
    assert "Daily Digest</b> · Sun, 6 Sep" in message
    assert "Internet 24h: 99.93%" in message
    assert "Energy yesterday: 18.42 kWh" in message
    assert "Backups: 6/6 OK" in message
    assert "Expenses: Rp185.500 · 7 transactions" in message
    assert "Vellum: ~$1.42 / $18.20" in message
    assert "DeepSeek: ~USD 0.21 / USD 8.72" in message
    assert "ChatGPT" not in message
    assert "All systems normal" in message


def test_idr_requires_whole_integer() -> None:
    assert format_idr(805630) == "Rp805.630"
    assert format_idr(1.5) == "unavailable"
    assert format_idr(True) == "unavailable"


def test_dynamic_backup_text_is_html_escaped() -> None:
    report = fixture_report(date(2026, 9, 6))
    backups = report.by_name("backups")
    assert backups is not None
    backups.status = Status.FAILED
    backups.data = {
        "ok_count": 0,
        "total_count": 1,
        "results": [{"name": "<bad>.log", "status": "failed", "reason": "A&B"}],
    }
    message = render_telegram(report)
    assert "&lt;bad&gt;.log" in message
    assert "A&amp;B" in message
    assert "<bad>" not in message


def test_missing_first_provider_snapshot_is_clear() -> None:
    report = fixture_report(date(2026, 9, 6))
    provider = report.by_name("openrouter")
    assert provider is not None
    provider.status = Status.UNAVAILABLE
    provider.error_code = "missing_start_snapshot"
    assert "OpenRouter: unavailable (missing midnight snapshot)" in render_telegram(report)


def test_backup_summary_includes_aggregate_without_per_log_success_lines() -> None:
    report = fixture_report(date(2026, 9, 6))
    backups = report.by_name("backups")
    assert backups is not None
    backups.data["transferred_total"] = "42.41 MiB"
    message = render_telegram(report)
    assert "Backups: 6/6 OK · 42.41 MiB transferred" in message
    assert "backup-1.log" not in message


def test_vellum_first_day_still_shows_current_balance() -> None:
    report = fixture_report(date(2026, 9, 11))
    vellum = report.by_name("vellum")
    assert vellum is not None
    vellum.status = Status.UNAVAILABLE
    vellum.error_code = "missing_start_snapshot"
    vellum.data = {"currency": "USD", "balance": "6.23"}
    assert "Vellum: usage unavailable (missing midnight snapshot) / $6.23" in render_telegram(
        report
    )
