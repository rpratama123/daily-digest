"""Disabled-by-default client for the documented expense digest endpoint."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from app.clock import DigestContext
from app.models import CollectorResult, Status

NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class _ExpenseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpenseCategory(_ExpenseModel):
    name: StrictStr = Field(min_length=1)
    total: NonNegativeInt


class ExpenseDailyResponse(_ExpenseModel):
    date: date
    timezone: StrictStr
    currency: StrictStr
    total: NonNegativeInt
    transaction_count: NonNegativeInt
    top_category: ExpenseCategory
    generated_at: datetime

    @field_validator("date", mode="before")
    @classmethod
    def date_is_iso_calendar_date(cls, value: object) -> object:
        if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            raise ValueError("date must be an ISO calendar date")
        return value

    @field_validator("generated_at")
    @classmethod
    def generated_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value


class ExpenseCollector:
    """Collect the confirmed daily summary without accessing expense storage."""

    def __init__(
        self,
        base_url: str,
        *,
        enabled: bool = False,
        host_header: str | None = None,
        username: str | None = None,
        password: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if (username is None) != (password is None):
            raise ValueError("expense Basic Auth requires both username and password")
        self.enabled = enabled
        self.host_header = host_header
        self._auth = httpx.BasicAuth(username, password) if username is not None else None
        self._owned_client = client is None
        self.client = client or httpx.Client(base_url=base_url.rstrip("/"))

    def close(self) -> None:
        if self._owned_client:
            self.client.close()

    def collect(self, context: DigestContext, observed_at: datetime) -> CollectorResult:
        if not self.enabled:
            return CollectorResult(
                name="expenses",
                status=Status.DISABLED,
                observed_at=observed_at,
                period_start=context.report_start,
                period_end=context.report_end,
            )

        try:
            response = self.client.get(
                "/api/digest/daily",
                params={"date": context.report_date.isoformat(), "timezone": context.timezone.key},
                auth=self._auth,
                headers={"Host": self.host_header} if self.host_header else None,
            )
            response.raise_for_status()
            payload = ExpenseDailyResponse.model_validate(response.json())
            if payload.date != context.report_date:
                raise ValueError("expense_report_date_mismatch")
            if payload.timezone != context.timezone.key:
                raise ValueError("expense_timezone_mismatch")
            if payload.currency != "IDR":
                raise ValueError("expense_currency_not_idr")
        except (httpx.HTTPError, ValueError, ValidationError):
            return CollectorResult(
                name="expenses",
                status=Status.UNAVAILABLE,
                observed_at=observed_at,
                period_start=context.report_start,
                period_end=context.report_end,
                error_code="invalid_expense_response",
            )

        return CollectorResult(
            name="expenses",
            status=Status.OK,
            observed_at=observed_at,
            period_start=context.report_start,
            period_end=context.report_end,
            data=payload.model_dump(mode="json"),
        )


def collect_expenses(
    collector: ExpenseCollector, context: DigestContext, observed_at: datetime
) -> CollectorResult:
    """Small integration wrapper that preserves the collector's disabled guard."""
    return collector.collect(context, observed_at)
