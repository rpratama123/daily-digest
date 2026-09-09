"""Shared structured domain models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class Status(StrEnum):
    OK = "ok"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    DISABLED = "disabled"


class BackupStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    NO_ENTRIES = "no_entries"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AwareModel(StrictModel):
    @field_validator("observed_at", "period_start", "period_end", check_fields=False)
    @classmethod
    def aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must be timezone-aware")
        return value


class Money(StrictModel):
    currency: str = Field(min_length=3, max_length=3)
    amount: Decimal

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return format(value, "f")


class BackupResult(StrictModel):
    name: str
    status: BackupStatus
    transferred: str | None = None
    reason: str | None = None
    selected_lines: int = 0
    selected_blocks: int = 0


class CollectorResult(AwareModel):
    name: str
    status: Status
    observed_at: datetime
    period_start: datetime | None = None
    period_end: datetime | None = None
    estimated: bool = False
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


class ProviderSnapshot(AwareModel):
    provider: str
    account_key: str = "default"
    boundary_date: date | None = None
    observed_at: datetime
    kind: str
    payload: dict[str, Any]
    canonical: bool = False


class DigestReport(AwareModel):
    delivery_date: date
    timezone: str
    report_start: datetime
    report_end: datetime
    collected_at: datetime
    collectors: list[CollectorResult]

    @field_validator("collected_at")
    @classmethod
    def aware_collected_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    def by_name(self, name: str) -> CollectorResult | None:
        return next((item for item in self.collectors if item.name == name), None)


class DeliveryState(StrEnum):
    CLAIMED = "claimed"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"


class DeliveryReceipt(AwareModel):
    state: DeliveryState
    observed_at: datetime
    message_id: int | None = None
    error_code: str | None = None
