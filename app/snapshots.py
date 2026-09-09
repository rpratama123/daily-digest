"""Provider snapshot comparison logic."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.clock import DigestContext
from app.models import CollectorResult, ProviderSnapshot, Status


def _unavailable(
    name: str,
    context: DigestContext,
    observed_at: datetime,
    code: str,
    data: dict[str, Any] | None = None,
) -> CollectorResult:
    return CollectorResult(
        name=name,
        status=Status.UNAVAILABLE,
        observed_at=observed_at,
        period_start=context.report_start,
        period_end=context.report_end,
        data=data or {},
        error_code=code,
    )


def _validate_pair(
    provider: str,
    kind: str,
    context: DigestContext,
    start: ProviderSnapshot | None,
    end: ProviderSnapshot | None,
) -> str | None:
    if start is None:
        return "missing_start_snapshot"
    if end is None:
        return "missing_end_snapshot"
    if (
        start.provider != provider
        or end.provider != provider
        or start.kind != kind
        or end.kind != kind
    ):
        return "invalid_snapshot"
    if start.account_key != end.account_key:
        return "snapshot_account_mismatch"
    if not start.canonical or not end.canonical:
        return "noncanonical_snapshot"
    if start.boundary_date != context.report_date or end.boundary_date != context.delivery_date:
        return "snapshot_boundary_mismatch"
    return None


def _amount(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ValueError
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError
    return amount


def compare_openrouter_snapshots(
    context: DigestContext,
    observed_at: datetime,
    start: ProviderSnapshot | None,
    end: ProviderSnapshot | None,
) -> CollectorResult:
    error = _validate_pair("openrouter", "credits", context, start, end)
    if error:
        return _unavailable("openrouter", context, observed_at, error)
    assert start is not None and end is not None
    try:
        start_usage = _amount(start.payload["total_usage"])
        end_usage = _amount(end.payload["total_usage"])
        total_credits = _amount(end.payload["total_credits"])
        balance = _amount(end.payload["balance"])
    except (KeyError, ValueError):
        return _unavailable("openrouter", context, observed_at, "invalid_snapshot_payload")
    usage = end_usage - start_usage
    if usage < 0:
        return _unavailable("openrouter", context, observed_at, "usage_counter_decreased")
    if balance != total_credits - end_usage:
        return _unavailable("openrouter", context, observed_at, "invalid_snapshot_payload")
    return CollectorResult(
        name="openrouter",
        status=Status.OK,
        observed_at=observed_at,
        period_start=context.report_start,
        period_end=context.report_end,
        data={
            "currency": "USD",
            "usage": format(usage, "f"),
            "balance": format(balance, "f"),
            "total_credits": format(total_credits, "f"),
            "total_usage": format(end_usage, "f"),
        },
    )


def _deepseek_balances(snapshot: ProviderSnapshot) -> dict[str, dict[str, Any]]:
    payload = snapshot.payload
    if not isinstance(payload.get("is_available"), bool) or not payload["is_available"]:
        raise ValueError
    infos = payload.get("balance_infos")
    if not isinstance(infos, list):
        raise ValueError
    result: dict[str, dict[str, Any]] = {}
    for info in infos:
        if not isinstance(info, dict) or not isinstance(info.get("currency"), str):
            raise ValueError
        currency = info["currency"]
        if not currency or currency in result:
            raise ValueError
        _amount(info.get("total_balance"))
        result[currency] = info
    return result


def compare_deepseek_snapshots(
    context: DigestContext,
    observed_at: datetime,
    start: ProviderSnapshot | None,
    end: ProviderSnapshot | None,
) -> CollectorResult:
    error = _validate_pair("deepseek", "balance", context, start, end)
    if error:
        return _unavailable("deepseek", context, observed_at, error)
    assert start is not None and end is not None
    try:
        start_balances = _deepseek_balances(start)
        end_balances = _deepseek_balances(end)
    except ValueError:
        return _unavailable("deepseek", context, observed_at, "invalid_snapshot_payload")
    if start_balances.keys() != end_balances.keys():
        return _unavailable("deepseek", context, observed_at, "currency_set_changed")

    balances: list[dict[str, str]] = []
    usage: list[dict[str, str]] = []
    adjustments: list[dict[str, str]] = []
    for currency in sorted(end_balances):
        start_total = _amount(start_balances[currency]["total_balance"])
        end_total = _amount(end_balances[currency]["total_balance"])
        balances.append({"currency": currency, "amount": format(end_total, "f")})
        change = start_total - end_total
        if change >= 0:
            usage.append({"currency": currency, "amount": format(change, "f")})
        else:
            adjustments.append({"currency": currency, "amount": format(-change, "f")})

    data = {"balances": balances, "usage": usage, "adjustments": adjustments}
    if adjustments:
        return _unavailable("deepseek", context, observed_at, "balance_adjustment_ambiguity", data)
    return CollectorResult(
        name="deepseek",
        status=Status.OK,
        observed_at=observed_at,
        period_start=context.report_start,
        period_end=context.report_end,
        estimated=True,
        data=data,
    )
