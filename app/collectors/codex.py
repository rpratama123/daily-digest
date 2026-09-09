"""Collector for sanitized Codex weekly-quota exports."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from app.models import CollectorResult, Status


class _ExportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeeklyQuota(_ExportModel):
    used_percent: Decimal
    remaining_percent: Decimal
    window_minutes: Literal[10080]
    resets_at: datetime

    @field_validator("used_percent", "remaining_percent")
    @classmethod
    def valid_percent(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or not Decimal(0) <= value <= Decimal(100):
            raise ValueError("percentage must be between zero and one hundred")
        return value

    @field_validator("resets_at")
    @classmethod
    def aware_reset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reset time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def percentages_match(self) -> WeeklyQuota:
        if self.used_percent + self.remaining_percent != Decimal(100):
            raise ValueError("used and remaining percentages must total one hundred")
        return self


class CodexQuotaExport(_ExportModel):
    schema_version: Literal[1]
    source: Literal["codex_app_server_rate_limits"]
    captured_at: datetime
    bucket_id: Literal["codex"]
    weekly: WeeklyQuota

    @field_validator("captured_at")
    @classmethod
    def aware_capture(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("capture time must be timezone-aware")
        return value


class CodexCollector:
    def __init__(
        self,
        export_file: Path,
        timezone: str,
        *,
        enabled: bool = False,
        max_file_bytes: int = 32_768,
        max_age: timedelta = timedelta(minutes=30),
    ) -> None:
        self.export_file = export_file
        self.timezone = ZoneInfo(timezone)
        self.enabled = enabled
        self.max_file_bytes = max_file_bytes
        self.max_age = max_age

    def collect(self, observed_at: datetime) -> CollectorResult:
        if not self.enabled:
            return CollectorResult(name="codex", status=Status.DISABLED, observed_at=observed_at)
        try:
            if (
                self.export_file.name != "latest.json"
                or not self.export_file.is_file()
                or self.export_file.is_symlink()
            ):
                raise ValueError("codex_export_missing")
            if self.export_file.stat().st_size > self.max_file_bytes:
                raise ValueError("codex_export_oversized")
            payload = json.loads(
                self.export_file.read_text(encoding="utf-8"),
                parse_float=Decimal,
                parse_int=Decimal,
            )
            export = CodexQuotaExport.model_validate(payload)
            if export.captured_at > observed_at or observed_at - export.captured_at > self.max_age:
                raise ValueError("codex_export_stale")
            if export.weekly.resets_at <= export.captured_at:
                raise ValueError("codex_reset_time_invalid")
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            return CollectorResult(
                name="codex",
                status=Status.UNAVAILABLE,
                observed_at=observed_at,
                error_code="codex_export_invalid",
            )
        except ValueError as exc:
            code = str(exc)
            status = Status.STALE if code == "codex_export_stale" else Status.UNAVAILABLE
            return CollectorResult(
                name="codex", status=status, observed_at=observed_at, error_code=code
            )

        reset_date: date = export.weekly.resets_at.astimezone(self.timezone).date()
        return CollectorResult(
            name="codex",
            status=Status.OK,
            observed_at=observed_at,
            data={
                "remaining_percent": format(export.weekly.remaining_percent, "f"),
                "window_minutes": export.weekly.window_minutes,
                "resets_at": export.weekly.resets_at.isoformat(),
                "reset_date": reset_date.isoformat(),
                "label": "weekly",
            },
        )
