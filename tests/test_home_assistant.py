import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from app.clock import DigestContext
from app.collectors.home_assistant import HomeAssistantCollector
from app.models import Status

FIXTURES = Path(__file__).parent / "fixtures" / "home_assistant"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def collector(payloads: dict[str, dict]) -> HomeAssistantCollector:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer fake-token"
        entity = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=payloads[entity])

    client = httpx.Client(base_url="http://ha.test", transport=httpx.MockTransport(handler))
    return HomeAssistantCollector(
        "http://ha.test",
        "fake-token",
        "sensor.daily_home_consumed_energy",
        "sensor.home_consumed_energy",
        "sensor.internet_conectivity_uptime",
        client,
    )


def test_energy_prefers_verified_last_period() -> None:
    instance = collector({"sensor.daily_home_consumed_energy": load("daily_energy.json")})
    result = instance.collect_energy(
        DigestContext.create(date(2026, 9, 9)), datetime(2026, 9, 9, tzinfo=UTC)
    )
    assert result.status == Status.OK
    assert result.data == {"kwh": "9.77", "source": "last_period"}
    assert result.estimated is False


def test_stale_last_period_uses_cumulative_fallback() -> None:
    payload = load("daily_energy.json")
    payload["attributes"]["last_reset"] = "2026-09-07T17:00:00+00:00"
    instance = collector({"sensor.daily_home_consumed_energy": payload})
    result = instance.collect_energy(
        DigestContext.create(date(2026, 9, 9)),
        datetime(2026, 9, 9, tzinfo=UTC),
        Decimal("100.25"),
        Decimal("109.75"),
    )
    assert result.status == Status.OK
    assert result.data["kwh"] == "9.50"
    assert result.estimated is True


def test_missing_fallback_is_unavailable() -> None:
    payload = load("daily_energy.json")
    payload["attributes"].pop("last_period")
    instance = collector({"sensor.daily_home_consumed_energy": payload})
    result = instance.collect_energy(
        DigestContext.create(date(2026, 9, 9)), datetime(2026, 9, 9, tzinfo=UTC)
    )
    assert result.status == Status.UNAVAILABLE
    assert result.error_code == "missing_cumulative_snapshots"


def test_internet_is_labeled_last_24h() -> None:
    instance = collector({"sensor.internet_conectivity_uptime": load("internet.json")})
    result = instance.collect_internet(datetime(2026, 9, 9, tzinfo=UTC))
    assert result.status == Status.OK
    assert result.data["label"] == "last 24h"


def test_invalid_unit_is_unavailable() -> None:
    payload = load("internet.json")
    payload["attributes"]["unit_of_measurement"] = "hours"
    instance = collector({"sensor.internet_conectivity_uptime": payload})
    result = instance.collect_internet(datetime(2026, 9, 9, tzinfo=UTC))
    assert result.status == Status.UNAVAILABLE


def test_cumulative_snapshot_uses_confirmed_schema() -> None:
    instance = collector({"sensor.home_consumed_energy": load("cumulative_energy.json")})
    assert instance.cumulative_snapshot() == (Decimal("5466.15"), "kWh")
