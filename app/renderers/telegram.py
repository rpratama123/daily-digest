"""Concise, safely escaped Telegram HTML renderer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape

from app.models import BackupStatus, CollectorResult, DigestReport, Status

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MAX_MESSAGE_LENGTH = 4096


def _number(value: object, places: int = 2) -> str:
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return "unavailable"
    rendered = f"{number:.{places}f}"
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _money(value: object) -> str:
    try:
        return f"{Decimal(str(value)):.2f}"
    except InvalidOperation:
        return "unavailable"


def format_idr(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        return "unavailable"
    return f"Rp{value:,}".replace(",", ".")


def _unavailable(label: str) -> str:
    return f"{label}: unavailable"


def _render_backups(result: CollectorResult) -> list[str]:
    data = result.data
    summary = f"💾 Backups: {data.get('ok_count', 0)}/{data.get('total_count', 0)} OK"
    transferred = data.get("transferred_total")
    if transferred:
        summary += f" · {escape(str(transferred))} transferred"
    lines = [summary]
    for item in data.get("results", []):
        if item.get("status") == BackupStatus.OK:
            continue
        name = escape(str(item.get("name", "backup")))
        reason = escape(str(item.get("reason") or item.get("status") or "failed"))
        lines.append(f"  • {name}: {reason}")
    return lines


def _provider_line(label: str, result: CollectorResult) -> str:
    if result.status != Status.OK:
        if result.error_code in {
            "missing_start_snapshot",
            "missing_end_snapshot",
            "vellum_snapshot_outside_midnight_tolerance",
        }:
            if result.name == "vellum" and result.data.get("balance") is not None:
                return (
                    f"{label}: usage unavailable (missing midnight snapshot) / "
                    f"${_money(result.data.get('balance'))}"
                )
            return f"{label}: unavailable (missing midnight snapshot)"
        if result.error_code == "balance_adjustment_ambiguity":
            if result.name == "vellum" and result.data.get("balance") is not None:
                return (
                    f"{label}: usage unavailable (balance adjustment) / "
                    f"${_money(result.data.get('balance'))}"
                )
            return f"{label}: unavailable (balance adjustment)"
        return _unavailable(label)
    data = result.data
    if result.name == "codex":
        try:
            reset_date = date.fromisoformat(str(data.get("reset_date")))
        except ValueError:
            return _unavailable(label)
        remaining = _number(data.get("remaining_percent"))
        if remaining == "unavailable":
            return _unavailable(label)
        reset = f"{WEEKDAYS[reset_date.weekday()]}, {reset_date.day} {MONTHS[reset_date.month - 1]}"
        return f"{label} weekly: {remaining}% left · resets {reset}"
    if result.name == "openrouter":
        return f"{label}: ${_money(data.get('usage'))} / ${_money(data.get('balance'))}"
    if result.name == "deepseek":
        usage = data.get("usage", [])
        balances = data.get("balances", [])
        chosen_usage = next(
            (item for item in usage if item.get("currency") == "USD"), usage[0] if usage else None
        )
        chosen_balance = next(
            (item for item in balances if item.get("currency") == "USD"),
            balances[0] if balances else None,
        )
        if not chosen_usage or not chosen_balance:
            return _unavailable(label)
        currency = escape(str(chosen_balance.get("currency", "")))
        marker = "~" if result.estimated else ""
        return (
            f"{label}: {marker}{currency} {_money(chosen_usage.get('amount'))} / "
            f"{currency} {_money(chosen_balance.get('amount'))}"
        )
    if result.name == "vellum":
        if data.get("usage") is None or data.get("balance") is None:
            return _unavailable(label)
        marker = "~" if result.estimated else ""
        return f"{label}: {marker}${_money(data.get('usage'))} / ${_money(data.get('balance'))}"
    return _unavailable(label)


def render_telegram(report: DigestReport) -> str:
    day = report.delivery_date
    lines = [
        f"☀️ <b>Daily Digest</b> · {WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]}",
        "",
    ]

    internet = report.by_name("internet")
    if internet and internet.status == Status.OK:
        lines.append(f"🌐 Internet 24h: {_number(internet.data.get('percent'))}%")
    elif internet and internet.status != Status.DISABLED:
        lines.append("🌐 Internet 24h: unavailable")

    energy = report.by_name("energy")
    if energy and energy.status == Status.OK:
        marker = "~" if energy.estimated else ""
        lines.append(f"⚡ Energy yesterday: {marker}{_number(energy.data.get('kwh'))} kWh")
    elif energy and energy.status != Status.DISABLED:
        lines.append("⚡ Energy yesterday: unavailable")

    backups = report.by_name("backups")
    if backups and backups.status != Status.DISABLED:
        lines.extend(_render_backups(backups))

    expenses = report.by_name("expenses")
    if expenses and expenses.status == Status.OK:
        total = format_idr(expenses.data.get("total"))
        count = int(expenses.data.get("transaction_count", 0))
        noun = "transaction" if count == 1 else "transactions"
        lines.append(f"💳 Expenses: {total} · {count} {noun}")
    elif expenses and expenses.status != Status.DISABLED:
        lines.append("💳 Expenses: unavailable")

    provider_names = (
        ("vellum", "Vellum"),
        ("openrouter", "OpenRouter"),
        ("deepseek", "DeepSeek"),
        ("codex", "Codex"),
    )
    providers = [(report.by_name(name), label) for name, label in provider_names]
    visible = [
        (result, label)
        for result, label in providers
        if result and result.status != Status.DISABLED
    ]
    if visible:
        lines.extend(["", "🤖 <b>AI usage / balance</b>"])
        lines.extend(
            _provider_line(label, result) for result, label in visible if result is not None
        )

    enabled = [item for item in report.collectors if item.status != Status.DISABLED]
    all_normal = bool(enabled) and all(item.status == Status.OK for item in enabled)
    lines.extend(
        [
            "",
            "✅ All systems normal" if all_normal else "⚠️ Some data unavailable or needs attention",
        ]
    )

    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE_LENGTH:
        retained: list[str] = []
        suffix = "… details truncated"
        for line in lines:
            candidate = "\n".join([*retained, line, suffix])
            if len(candidate) > MAX_MESSAGE_LENGTH:
                break
            retained.append(line)
        message = "\n".join([*retained, suffix])
    return message
