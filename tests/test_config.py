from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import BackupSettings, Settings


def test_disabled_integrations_do_not_require_secret_files(tmp_path: Path) -> None:
    settings = Settings(
        state_directory=tmp_path,
        home_assistant={"enabled": False},
        openrouter={"enabled": False},
        deepseek={"enabled": False},
        telegram={"enabled": False},
    )
    assert settings.database_path == tmp_path / "daily-digest.sqlite3"


@pytest.mark.parametrize("name", ["../x.log", "/tmp/x.log", "nested/x.log", ""])
def test_backup_names_must_be_plain_filenames(name: str) -> None:
    with pytest.raises(ValidationError):
        BackupSettings(logs=[name])


def test_duplicate_backup_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        BackupSettings(logs=["same.log", "same.log"])


def test_yaml_loads_and_environment_overrides(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "timezone: UTC\nhome_assistant:\n  enabled: false\nbackups:\n  enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DAILY_DIGEST_CONFIG", str(config))
    monkeypatch.setenv("DAILY_DIGEST_TIMEZONE", "Asia/Jakarta")
    settings = Settings()
    assert settings.timezone == "Asia/Jakarta"
    assert settings.home_assistant.enabled is False


def test_unknown_yaml_keys_are_rejected(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "unknown_setting: true\nhome_assistant:\n  enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DAILY_DIGEST_CONFIG", str(config))
    with pytest.raises(ValidationError):
        Settings()
