"""Typed application configuration and secret-file access."""

from __future__ import annotations

import os
from pathlib import Path, PurePath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

DEFAULT_BACKUP_LOGS = [
    "rclone-appdata-backup.log",
    "rclone-cikopopup-backup.log",
    "rclone-photos-backup.log",
    "rclone-cikonet-backup.log",
    "rclone-paperlessngx-backup.log",
    "rclone-stash-backup.log",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpSettings(StrictModel):
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=10.0, gt=0, le=120)


class HomeAssistantSettings(StrictModel):
    enabled: bool = True
    base_url: str = "http://10.20.30.55:8123"
    token_file: Path = Path("/run/secrets/daily-digest/home_assistant_token")
    daily_energy_entity: str = "sensor.daily_home_consumed_energy"
    cumulative_energy_entity: str = "sensor.home_consumed_energy"
    internet_entity: str = "sensor.internet_conectivity_uptime"


class BackupSettings(StrictModel):
    enabled: bool = True
    directory: Path = Path("/data/rclone-logs")
    logs: list[str] = Field(default_factory=lambda: list(DEFAULT_BACKUP_LOGS))

    @field_validator("logs")
    @classmethod
    def validate_logs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("backup log names must be unique")
        for name in value:
            path = PurePath(name)
            if not name or path.is_absolute() or len(path.parts) != 1 or name in {".", ".."}:
                raise ValueError("backup log names must be plain filenames")
        return value


class ExpenseSettings(StrictModel):
    enabled: bool = False
    base_url: str = "http://expenses-dashboard:8000"
    host_header: str | None = "expenses.cikohome.stream"
    username_file: Path | None = None
    password_file: Path | None = None


class ProviderSettings(StrictModel):
    enabled: bool = False
    api_key_file: Path


class OpenRouterSettings(ProviderSettings):
    api_key_file: Path = Path("/run/secrets/daily-digest/openrouter_management_key")
    base_url: str = "https://openrouter.ai"


class DeepSeekSettings(ProviderSettings):
    api_key_file: Path = Path("/run/secrets/daily-digest/deepseek_api_key")
    base_url: str = "https://api.deepseek.com"


class VellumSettings(StrictModel):
    enabled: bool = False
    export_directory: Path = Path("/data/vellum-exports")
    max_file_bytes: int = Field(default=131_072, gt=0, le=1_048_576)
    max_age_hours: int = Field(default=36, gt=0, le=168)


class TelegramSettings(StrictModel):
    enabled: bool = False
    bot_token_file: Path = Path("/run/secrets/daily-digest/telegram_bot_token")
    chat_id_file: Path = Path("/run/secrets/daily-digest/telegram_chat_id")


class EscPosSettings(StrictModel):
    enabled: Literal[False] = False


class ScheduleSettings(StrictModel):
    snapshot_hour: int = Field(default=0, ge=0, le=23)
    digest_hour: int = Field(default=7, ge=0, le=23)
    misfire_grace_seconds: int = Field(default=3600, ge=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DAILY_DIGEST_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    timezone: str = "Asia/Jakarta"
    state_directory: Path = Path("/data/state")
    database_name: str = "daily-digest.sqlite3"
    snapshot_tolerance_seconds: int = Field(default=120, ge=0, le=3600)
    http: HttpSettings = Field(default_factory=HttpSettings)
    home_assistant: HomeAssistantSettings = Field(default_factory=HomeAssistantSettings)
    backups: BackupSettings = Field(default_factory=BackupSettings)
    expenses: ExpenseSettings = Field(default_factory=ExpenseSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)
    vellum: VellumSettings = Field(default_factory=VellumSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    escpos: EscPosSettings = Field(default_factory=EscPosSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)

    @field_validator("database_name")
    @classmethod
    def validate_database_name(cls, value: str) -> str:
        if not value or PurePath(value).name != value:
            raise ValueError("database_name must be a plain filename")
        return value

    @model_validator(mode="after")
    def validate_enabled_credentials(self) -> Settings:
        checks = [
            (self.home_assistant.enabled, self.home_assistant.token_file, "Home Assistant token"),
            (self.openrouter.enabled, self.openrouter.api_key_file, "OpenRouter key"),
            (self.deepseek.enabled, self.deepseek.api_key_file, "DeepSeek key"),
            (self.telegram.enabled, self.telegram.bot_token_file, "Telegram token"),
            (self.telegram.enabled, self.telegram.chat_id_file, "Telegram chat ID"),
        ]
        for enabled, path, label in checks:
            if enabled and not path.is_file():
                raise ValueError(f"{label} file is unavailable")
        if self.expenses.enabled and (
            (self.expenses.username_file is None) != (self.expenses.password_file is None)
        ):
            raise ValueError("expense Basic Auth requires both username and password files")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        config_path = Path(os.environ.get("DAILY_DIGEST_CONFIG", "config.yaml"))
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if config_path.is_file():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=config_path))
        return tuple(sources)

    @property
    def database_path(self) -> Path:
        return self.state_directory / self.database_name


def read_secret(path: Path) -> SecretStr:
    """Read one configured secret without logging or retaining its trailing newline."""
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("configured secret file is empty")
    return SecretStr(value)


def load_settings() -> Settings:
    return Settings()
