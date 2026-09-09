"""Home Assistant state collector."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.clock import DigestContext
from app.models import CollectorResult, Status


class HomeAssistantCollector:
    def __init__(
        self,
        base_url: str,
        token: str,
        daily_energy_entity: str,
        cumulative_energy_entity: str,
        internet_entity: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.daily_energy_entity = daily_energy_entity
        self.cumulative_energy_entity = cumulative_energy_entity
        self.internet_entity = internet_entity
        self._token = token
        self._owned_client = client is None
        self.client = client or httpx.Client(base_url=base_url.rstrip("/"))

    def close(self) -> None:
        if self._owned_client:
            self.client.close()

    def fetch_entity(self, entity_id: str) -> dict[str, Any]:
        response = self.client.get(
            f"/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("entity_id") != entity_id:
            raise ValueError("invalid_entity_response")
        return payload

    @staticmethod
    def _decimal_state(payload: dict[str, Any]) -> Decimal:
        state = payload.get("state")
        if state in {None, "unknown", "unavailable"}:
            raise ValueError("state_unavailable")
        try:
            return Decimal(str(state))
        except InvalidOperation as exc:
            raise ValueError("state_not_numeric") from exc

    @staticmethod
    def _require_unit(payload: dict[str, Any], expected: str) -> None:
        if payload.get("attributes", {}).get("unit_of_measurement") != expected:
            raise ValueError("unexpected_unit")

    def collect_energy(
        self,
        context: DigestContext,
        observed_at: datetime,
        cumulative_start: Decimal | None = None,
        cumulative_end: Decimal | None = None,
    ) -> CollectorResult:
        try:
            daily = self.fetch_entity(self.daily_energy_entity)
            self._require_unit(daily, "kWh")
            attrs = daily.get("attributes", {})
            last_period = attrs.get("last_period")
            last_reset_raw = attrs.get("last_reset")
            if last_period is not None and last_reset_raw:
                last_reset = datetime.fromisoformat(str(last_reset_raw))
                if last_reset.tzinfo is None or last_reset.utcoffset() is None:
                    raise ValueError("naive_last_reset")
                if last_reset.astimezone(context.timezone).date() == context.delivery_date:
                    value = Decimal(str(last_period))
                    if value < 0:
                        raise ValueError("negative_energy")
                    return CollectorResult(
                        name="energy",
                        status=Status.OK,
                        observed_at=observed_at,
                        period_start=context.report_start,
                        period_end=context.report_end,
                        data={"kwh": format(value, "f"), "source": "last_period"},
                    )

            if cumulative_start is None or cumulative_end is None:
                raise ValueError("missing_cumulative_snapshots")
            value = cumulative_end - cumulative_start
            if value < 0:
                raise ValueError("cumulative_counter_reset")
            return CollectorResult(
                name="energy",
                status=Status.OK,
                observed_at=observed_at,
                period_start=context.report_start,
                period_end=context.report_end,
                estimated=True,
                data={"kwh": format(value, "f"), "source": "cumulative_snapshots"},
            )
        except (httpx.HTTPError, ValueError, InvalidOperation) as exc:
            code = str(exc) if isinstance(exc, ValueError) else "home_assistant_http_error"
            return CollectorResult(
                name="energy",
                status=Status.UNAVAILABLE,
                observed_at=observed_at,
                period_start=context.report_start,
                period_end=context.report_end,
                error_code=code,
            )

    def collect_internet(self, observed_at: datetime) -> CollectorResult:
        try:
            payload = self.fetch_entity(self.internet_entity)
            self._require_unit(payload, "%")
            value = self._decimal_state(payload)
            if not Decimal("0") <= value <= Decimal("100"):
                raise ValueError("internet_out_of_range")
            return CollectorResult(
                name="internet",
                status=Status.OK,
                observed_at=observed_at,
                data={"percent": format(value, "f"), "label": "last 24h"},
            )
        except (httpx.HTTPError, ValueError) as exc:
            code = str(exc) if isinstance(exc, ValueError) else "home_assistant_http_error"
            return CollectorResult(
                name="internet",
                status=Status.UNAVAILABLE,
                observed_at=observed_at,
                error_code=code,
            )

    def cumulative_snapshot(self) -> tuple[Decimal, str]:
        payload = self.fetch_entity(self.cumulative_energy_entity)
        self._require_unit(payload, "kWh")
        return self._decimal_state(payload), "kWh"
