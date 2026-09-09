from pathlib import Path

from app.clock import utc_now
from app.main import main
from app.scheduler import write_heartbeat


def disabled_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DAILY_DIGEST_STATE_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("DAILY_DIGEST_HOME_ASSISTANT__ENABLED", "false")
    monkeypatch.setenv("DAILY_DIGEST_BACKUPS__ENABLED", "false")


def test_fixture_dry_run_never_needs_delivery(monkeypatch, tmp_path: Path, capsys) -> None:
    disabled_environment(monkeypatch, tmp_path)
    assert main(["send", "--date", "2026-09-06", "--dry-run", "--fixture"]) == 0
    output = capsys.readouterr().out
    assert "Daily Digest · Sun, 6 Sep" in output
    assert "Rp185.500" in output


def test_healthcheck_uses_local_storage(monkeypatch, tmp_path: Path, capsys) -> None:
    disabled_environment(monkeypatch, tmp_path)
    write_heartbeat(tmp_path / "scheduler-heartbeat", utc_now())
    assert main(["healthcheck"]) == 0
    assert capsys.readouterr().out.strip() == "healthy"
