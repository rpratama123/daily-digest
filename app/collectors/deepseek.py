"""DeepSeek balance snapshot collector."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.models import ProviderSnapshot


class DeepSeekError(RuntimeError):
    """A DeepSeek failure safe to expose in collector output."""


def _valid_amount(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return False
    return amount.is_finite() and amount >= 0


def _validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("is_available"), bool):
        raise ValueError
    balances = payload.get("balance_infos")
    if not isinstance(balances, list):
        raise ValueError
    currencies: set[str] = set()
    for balance in balances:
        if not isinstance(balance, dict):
            raise ValueError
        currency = balance.get("currency")
        if not isinstance(currency, str) or not currency or currency in currencies:
            raise ValueError
        currencies.add(currency)
        if not all(
            _valid_amount(balance.get(field))
            for field in ("total_balance", "granted_balance", "topped_up_balance")
        ):
            raise ValueError
    return payload


class DeepSeekCollector:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
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
                "/user/balance",
                headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            raise DeepSeekError("deepseek_http_error") from None

        try:
            payload = _validate_payload(json.loads(response.content))
        except (json.JSONDecodeError, ValueError):
            raise DeepSeekError("deepseek_invalid_response") from None

        return ProviderSnapshot(
            provider="deepseek",
            account_key=account_key,
            boundary_date=boundary_date,
            observed_at=observed_at,
            kind="balance",
            payload=payload,
            canonical=canonical,
        )
