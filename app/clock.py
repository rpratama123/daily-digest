"""Timezone-aware reporting periods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


@dataclass(frozen=True)
class DigestContext:
    delivery_date: date
    timezone: ZoneInfo

    @classmethod
    def create(cls, delivery_date: date, timezone_name: str = "Asia/Jakarta") -> DigestContext:
        return cls(delivery_date=delivery_date, timezone=ZoneInfo(timezone_name))

    @property
    def report_date(self) -> date:
        return self.delivery_date - timedelta(days=1)

    @property
    def report_start(self) -> datetime:
        return datetime.combine(self.report_date, time.min, self.timezone)

    @property
    def report_end(self) -> datetime:
        return datetime.combine(self.delivery_date, time.min, self.timezone)


def utc_now() -> datetime:
    return datetime.now(UTC)
