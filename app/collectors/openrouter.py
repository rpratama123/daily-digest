"""OpenRouter credit snapshot collector."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.models import ProviderSnapshot


class OpenRouterError(RuntimeError):
    """An OpenRouter failure safe to expose in collector output."""


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError from exc
    if not result.is_finite() or result < 0:
        raise ValueError
    return result


class OpenRouterCollector:
    def __init__(
        self,
        base_url: str,
        management_key: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._management_key = management_key
        self._owned_client = client is None
        self.client = client or httpx.Client(base_url=base_url.rstrip("/"))

    def close(self) -> None:
        if self._owned_client:
            self.client.close()

    def snapshot(
        self,
        observed_at: datetime,
        *,
        boundary_date: date | None = None,
        canonical: bool = False,
        account_key: str = "default",
    ) -> ProviderSnapshot:
        try:
            response = self.client.get(
                "/api/v1/credits",
                headers={
                    "Authorization": f"Bearer {self._management_key}",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError:
            raise OpenRouterError("openrouter_http_error") from None

        try:
            payload = json.loads(response.content, parse_float=Decimal, parse_int=Decimal)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                raise ValueError
            data = payload["data"]
            total_credits = _decimal(data["total_credits"])
            total_usage = _decimal(data["total_usage"])
            balance = total_credits - total_usage
            if balance < 0:
                raise ValueError
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise OpenRouterError("openrouter_invalid_response") from None

        return ProviderSnapshot(
            provider="openrouter",
            account_key=account_key,
            boundary_date=boundary_date,
            observed_at=observed_at,
            kind="credits",
            payload={
                "total_credits": format(total_credits, "f"),
                "total_usage": format(total_usage, "f"),
                "balance": format(balance, "f"),
            },
            canonical=canonical,
        )
