"""Collection, snapshot, rendering, and delivery orchestration."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx

from app.clock import DigestContext, utc_now
from app.collectors.backups import collect_backups
from app.collectors.deepseek import DeepSeekCollector
from app.collectors.expenses import ExpenseCollector
from app.collectors.home_assistant import HomeAssistantCollector
from app.collectors.openrouter import OpenRouterCollector
from app.collectors.vellum import VellumCollector
from app.config import Settings, read_secret
from app.deliverers.telegram import (
    TelegramDefinitiveError,
    TelegramDeliverer,
    TelegramUnknownError,
)
from app.imports.vellum import DirectoryVellumSource
from app.models import CollectorResult, DeliveryState, DigestReport, ProviderSnapshot, Status
from app.renderers.telegram import render_telegram
from app.snapshots import compare_deepseek_snapshots, compare_openrouter_snapshots
from app.storage import Storage


def _disabled(
    name: str, observed_at: datetime, context: DigestContext | None = None
) -> CollectorResult:
    return CollectorResult(
        name=name,
        status=Status.DISABLED,
        observed_at=observed_at,
        period_start=context.report_start if context else None,
        period_end=context.report_end if context else None,
    )


def _unavailable(
    name: str, observed_at: datetime, context: DigestContext, code: str
) -> CollectorResult:
    return CollectorResult(
        name=name,
        status=Status.UNAVAILABLE,
        observed_at=observed_at,
        period_start=context.report_start,
        period_end=context.report_end,
        error_code=code,
    )


class DigestService:
    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage
        self.timezone = ZoneInfo(settings.timezone)

    def _http_client(self, base_url: str) -> httpx.Client:
        return httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(
                self.settings.http.read_timeout_seconds,
                connect=self.settings.http.connect_timeout_seconds,
            ),
        )

    def _cumulative_energy_pair(
        self, context: DigestContext
    ) -> tuple[Decimal | None, Decimal | None]:
        values: list[Decimal | None] = []
        for boundary_date in (context.report_date, context.delivery_date):
            snapshot = self.storage.get_snapshot(
                "home_assistant", boundary_date, "cumulative_energy"
            )
            try:
                if snapshot is None or snapshot.payload.get("unit") != "kWh":
                    raise ValueError
                value = Decimal(str(snapshot.payload["value"]))
                if not value.is_finite() or value < 0:
                    raise ValueError
                values.append(value)
            except (KeyError, InvalidOperation, ValueError):
                values.append(None)
        return values[0], values[1]

    def collect(self, delivery_date: date, observed_at: datetime | None = None) -> DigestReport:
        observed_at = observed_at or utc_now()
        context = DigestContext.create(delivery_date, self.settings.timezone)
        run_id = self.storage.start_run("collect", delivery_date)
        results: list[CollectorResult] = []

        if self.settings.home_assistant.enabled:
            try:
                token = read_secret(self.settings.home_assistant.token_file).get_secret_value()
                client = self._http_client(self.settings.home_assistant.base_url)
                collector = HomeAssistantCollector(
                    self.settings.home_assistant.base_url,
                    token,
                    self.settings.home_assistant.daily_energy_entity,
                    self.settings.home_assistant.cumulative_energy_entity,
                    self.settings.home_assistant.internet_entity,
                    client,
                )
                results.extend(
                    [
                        collector.collect_energy(
                            context, observed_at, *self._cumulative_energy_pair(context)
                        ),
                        collector.collect_internet(observed_at),
                    ]
                )
                client.close()
            except (OSError, ValueError, httpx.HTTPError):
                results.extend(
                    [
                        _unavailable("energy", observed_at, context, "home_assistant_unavailable"),
                        _unavailable(
                            "internet", observed_at, context, "home_assistant_unavailable"
                        ),
                    ]
                )
        else:
            results.extend(
                [
                    _disabled("energy", observed_at, context),
                    _disabled("internet", observed_at, context),
                ]
            )

        if self.settings.backups.enabled:
            results.append(
                collect_backups(
                    self.settings.backups.directory,
                    self.settings.backups.logs,
                    context.report_date,
                    observed_at,
                    context.report_start,
                    context.report_end,
                )
            )
        else:
            results.append(_disabled("backups", observed_at, context))

        expense_settings = self.settings.expenses
        if expense_settings.enabled:
            try:
                username = (
                    read_secret(expense_settings.username_file).get_secret_value()
                    if expense_settings.username_file
                    else None
                )
                password = (
                    read_secret(expense_settings.password_file).get_secret_value()
                    if expense_settings.password_file
                    else None
                )
                expense_client = self._http_client(expense_settings.base_url)
                try:
                    expenses = ExpenseCollector(
                        expense_settings.base_url,
                        enabled=True,
                        host_header=expense_settings.host_header,
                        username=username,
                        password=password,
                        client=expense_client,
                    )
                    results.append(expenses.collect(context, observed_at))
                finally:
                    expense_client.close()
            except (OSError, ValueError, httpx.HTTPError):
                results.append(
                    _unavailable("expenses", observed_at, context, "expenses_unavailable")
                )
        else:
            results.append(_disabled("expenses", observed_at, context))

        if self.settings.openrouter.enabled:
            results.append(
                compare_openrouter_snapshots(
                    context,
                    observed_at,
                    self.storage.get_snapshot("openrouter", context.report_date, "credits"),
                    self.storage.get_snapshot("openrouter", context.delivery_date, "credits"),
                )
            )
        else:
            results.append(_disabled("openrouter", observed_at, context))

        if self.settings.deepseek.enabled:
            results.append(
                compare_deepseek_snapshots(
                    context,
                    observed_at,
                    self.storage.get_snapshot("deepseek", context.report_date, "balance"),
                    self.storage.get_snapshot("deepseek", context.delivery_date, "balance"),
                )
            )
        else:
            results.append(_disabled("deepseek", observed_at, context))

        vellum = VellumCollector(
            DirectoryVellumSource(self.settings.vellum.export_directory),
            enabled=self.settings.vellum.enabled,
            max_file_bytes=self.settings.vellum.max_file_bytes,
            max_age=timedelta(hours=self.settings.vellum.max_age_hours),
            midnight_tolerance=timedelta(seconds=self.settings.snapshot_tolerance_seconds),
        )
        results.append(vellum.collect(context, observed_at))
        results.append(_disabled("chatgpt", observed_at, context))

        report = DigestReport(
            delivery_date=delivery_date,
            timezone=self.settings.timezone,
            report_start=context.report_start,
            report_end=context.report_end,
            collected_at=observed_at,
            collectors=results,
        )
        rendered = render_telegram(report)
        for result in results:
            self.storage.save_collector_result(
                run_id, result.model_dump_json(), result.name, result.status
            )
        self.storage.save_digest(report, rendered)
        self.storage.finish_run(
            run_id,
            "ok" if all(r.status in {Status.OK, Status.DISABLED} for r in results) else "partial",
        )
        return report

    def snapshot(
        self, *, canonical: bool = False, observed_at: datetime | None = None
    ) -> list[str]:
        observed_at = observed_at or utc_now()
        local = observed_at.astimezone(self.timezone)
        boundary = local.replace(hour=0, minute=0, second=0, microsecond=0)
        within_tolerance = (
            abs((local - boundary).total_seconds()) <= self.settings.snapshot_tolerance_seconds
        )
        is_canonical = canonical and within_tolerance
        boundary_date = local.date() if is_canonical else None
        saved: list[str] = []
        run_id = self.storage.start_run("snapshot")
        failed = False

        if self.settings.home_assistant.enabled:
            try:
                token = read_secret(self.settings.home_assistant.token_file).get_secret_value()
                client = self._http_client(self.settings.home_assistant.base_url)
                collector = HomeAssistantCollector(
                    self.settings.home_assistant.base_url,
                    token,
                    self.settings.home_assistant.daily_energy_entity,
                    self.settings.home_assistant.cumulative_energy_entity,
                    self.settings.home_assistant.internet_entity,
                    client,
                )
                value, unit = collector.cumulative_snapshot()
                self.storage.save_snapshot(
                    ProviderSnapshot(
                        provider="home_assistant",
                        observed_at=observed_at,
                        boundary_date=boundary_date,
                        kind="cumulative_energy",
                        payload={"value": format(value, "f"), "unit": unit},
                        canonical=is_canonical,
                    )
                )
                saved.append("home_assistant")
                client.close()
            except (OSError, ValueError, httpx.HTTPError):
                failed = True

        if self.settings.openrouter.enabled:
            try:
                key = read_secret(self.settings.openrouter.api_key_file).get_secret_value()
                client = self._http_client(self.settings.openrouter.base_url)
                collector = OpenRouterCollector(self.settings.openrouter.base_url, key, client)
                self.storage.save_snapshot(
                    collector.snapshot(
                        observed_at, boundary_date=boundary_date, canonical=is_canonical
                    )
                )
                saved.append("openrouter")
                client.close()
            except (OSError, ValueError, RuntimeError, httpx.HTTPError):
                failed = True

        if self.settings.deepseek.enabled:
            try:
                key = read_secret(self.settings.deepseek.api_key_file).get_secret_value()
                client = self._http_client(self.settings.deepseek.base_url)
                collector = DeepSeekCollector(self.settings.deepseek.base_url, key, client)
                self.storage.save_snapshot(
                    collector.snapshot(
                        observed_at, boundary_date=boundary_date, canonical=is_canonical
                    )
                )
                saved.append("deepseek")
                client.close()
            except (OSError, ValueError, RuntimeError, httpx.HTTPError):
                failed = True
        self.storage.finish_run(run_id, "partial" if failed else "ok")
        return saved

    def send(self, delivery_date: date, *, dry_run: bool = False) -> str:
        stored = self.storage.get_latest_digest(delivery_date, self.settings.timezone)
        report = stored[1] if stored else self.collect(delivery_date)
        message = render_telegram(report)
        if dry_run:
            return message
        if not self.settings.telegram.enabled:
            raise RuntimeError("Telegram delivery is disabled")

        token = read_secret(self.settings.telegram.bot_token_file).get_secret_value()
        chat_id = read_secret(self.settings.telegram.chat_id_file).get_secret_value()
        destination_key = hashlib.sha256(chat_id.encode()).hexdigest()[:16]
        digest_id = (
            stored[0]
            if stored
            else self.storage.get_latest_digest(delivery_date, self.settings.timezone)[0]
        )
        now = utc_now()
        delivery_id = self.storage.claim_delivery(
            digest_id, delivery_date, self.settings.timezone, "telegram", destination_key, now
        )
        if delivery_id is None:
            raise RuntimeError("delivery already exists for this date")

        client = self._http_client(f"https://api.telegram.org/bot{token}")
        deliverer = TelegramDeliverer(token, chat_id, now, client)
        try:
            deliverer.validate_private_chat()
        except TelegramDefinitiveError:
            self.storage.set_delivery_state(delivery_id, DeliveryState.FAILED, utc_now())
            raise
        self.storage.set_delivery_state(delivery_id, DeliveryState.SENDING, utc_now())
        attempt_id = self.storage.start_delivery_attempt(delivery_id, utc_now())
        try:
            receipt = deliverer.deliver(message)
        except TelegramUnknownError as exc:
            self.storage.finish_delivery_attempt(attempt_id, utc_now(), "unknown", str(exc))
            self.storage.set_delivery_state(delivery_id, DeliveryState.UNKNOWN, utc_now())
            raise
        except TelegramDefinitiveError as exc:
            self.storage.finish_delivery_attempt(attempt_id, utc_now(), "failed", str(exc))
            self.storage.set_delivery_state(delivery_id, DeliveryState.FAILED, utc_now())
            raise
        finally:
            client.close()
        self.storage.finish_delivery_attempt(attempt_id, utc_now(), "sent")
        self.storage.set_delivery_state(
            delivery_id, DeliveryState.SENT, utc_now(), receipt.message_id
        )
        return message


def fixture_report(delivery_date: date, timezone: str = "Asia/Jakarta") -> DigestReport:
    now = utc_now()
    context = DigestContext.create(delivery_date, timezone)

    def result(name: str, data: dict, estimated: bool = False) -> CollectorResult:
        return CollectorResult(
            name=name,
            status=Status.OK,
            observed_at=now,
            period_start=context.report_start,
            period_end=context.report_end,
            estimated=estimated,
            data=data,
        )

    backups = [
        {"name": f"backup-{i}.log", "status": "ok", "transferred": None, "reason": None}
        for i in range(1, 7)
    ]
    return DigestReport(
        delivery_date=delivery_date,
        timezone=timezone,
        report_start=context.report_start,
        report_end=context.report_end,
        collected_at=now,
        collectors=[
            result("internet", {"percent": "99.93", "label": "last 24h"}),
            result("energy", {"kwh": "18.42", "source": "last_period"}),
            result("backups", {"ok_count": 6, "total_count": 6, "results": backups}),
            result("expenses", {"currency": "IDR", "total": 185500, "transaction_count": 7}),
            result(
                "vellum",
                {
                    "currency": "USD",
                    "usage": "1.42",
                    "balance": "18.20",
                    "source": "remaining_balance_delta",
                },
                True,
            ),
            result("openrouter", {"currency": "USD", "usage": "0.87", "balance": "12.43"}),
            result(
                "deepseek",
                {
                    "usage": [{"currency": "USD", "amount": "0.21"}],
                    "balances": [{"currency": "USD", "amount": "8.72"}],
                },
                True,
            ),
            _disabled("chatgpt", now, context),
        ],
    )
