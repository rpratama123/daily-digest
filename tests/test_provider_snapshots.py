import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from app.clock import DigestContext
from app.collectors.deepseek import DeepSeekCollector, DeepSeekError
from app.collectors.openrouter import OpenRouterCollector, OpenRouterError
from app.models import ProviderSnapshot, Status
from app.snapshots import compare_deepseek_snapshots, compare_openrouter_snapshots

FIXTURES = Path(__file__).parent / "fixtures" / "providers"
OBSERVED_AT = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
CONTEXT = DigestContext.create(date(2026, 9, 9))


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def response_client(payload: dict | bytes, expected_path: str, expected_key: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == expected_path
        assert request.headers["Authorization"] == f"Bearer {expected_key}"
        assert request.headers["Accept"] == "application/json"
        if isinstance(payload, bytes):
            return httpx.Response(
                200, content=payload, headers={"Content-Type": "application/json"}
            )
        return httpx.Response(200, json=payload)

    return httpx.Client(base_url="https://provider.test", transport=httpx.MockTransport(handler))


def openrouter_snapshot(name: str, boundary: date) -> ProviderSnapshot:
    instance = OpenRouterCollector(
        "https://provider.test",
        "management-secret",
        response_client((FIXTURES / name).read_bytes(), "/api/v1/credits", "management-secret"),
    )
    return instance.snapshot(OBSERVED_AT, boundary_date=boundary, canonical=True)


def deepseek_snapshot(name: str, boundary: date) -> ProviderSnapshot:
    payload = load(name)
    instance = DeepSeekCollector(
        "https://provider.test",
        "api-secret",
        response_client(payload, "/user/balance", "api-secret"),
    )
    snapshot = instance.snapshot(OBSERVED_AT, boundary_date=boundary, canonical=True)
    assert snapshot.payload == payload
    return snapshot


def test_openrouter_fetches_credits_with_management_bearer_and_exact_decimals() -> None:
    snapshot = openrouter_snapshot("openrouter_end.json", CONTEXT.delivery_date)

    assert snapshot.payload == {
        "total_credits": "25.0000",
        "total_usage": "11.3750",
        "balance": "13.6250",
    }
    assert snapshot.observed_at.tzinfo is not None


def test_openrouter_usage_is_between_exact_canonical_boundaries() -> None:
    start = openrouter_snapshot("openrouter_start.json", CONTEXT.report_date)
    end = openrouter_snapshot("openrouter_end.json", CONTEXT.delivery_date)

    result = compare_openrouter_snapshots(CONTEXT, OBSERVED_AT, start, end)

    assert result.status == Status.OK
    assert result.period_start == CONTEXT.report_start
    assert result.period_end == CONTEXT.report_end
    assert result.data == {
        "currency": "USD",
        "usage": "1.2500",
        "balance": "13.6250",
        "total_credits": "25.0000",
        "total_usage": "11.3750",
    }


def test_openrouter_missing_first_snapshot_is_explicitly_unavailable() -> None:
    end = openrouter_snapshot("openrouter_end.json", CONTEXT.delivery_date)
    result = compare_openrouter_snapshots(CONTEXT, OBSERVED_AT, None, end)

    assert result.status == Status.UNAVAILABLE
    assert result.error_code == "missing_start_snapshot"


def test_openrouter_counter_decrease_is_invalid() -> None:
    start = openrouter_snapshot("openrouter_end.json", CONTEXT.report_date)
    end = openrouter_snapshot("openrouter_start.json", CONTEXT.delivery_date)
    result = compare_openrouter_snapshots(CONTEXT, OBSERVED_AT, start, end)

    assert result.status == Status.UNAVAILABLE
    assert result.error_code == "usage_counter_decreased"


def test_noncanonical_or_wrong_boundary_snapshots_are_rejected() -> None:
    start = openrouter_snapshot("openrouter_start.json", CONTEXT.report_date)
    end = openrouter_snapshot("openrouter_end.json", CONTEXT.delivery_date)
    start.canonical = False
    assert (
        compare_openrouter_snapshots(CONTEXT, OBSERVED_AT, start, end).error_code
        == "noncanonical_snapshot"
    )
    start.canonical = True
    start.boundary_date = CONTEXT.delivery_date
    assert (
        compare_openrouter_snapshots(CONTEXT, OBSERVED_AT, start, end).error_code
        == "snapshot_boundary_mismatch"
    )


def test_deepseek_stores_complete_payload_and_compares_each_currency() -> None:
    start = deepseek_snapshot("deepseek_start.json", CONTEXT.report_date)
    end = deepseek_snapshot("deepseek_end.json", CONTEXT.delivery_date)

    result = compare_deepseek_snapshots(CONTEXT, OBSERVED_AT, start, end)

    assert result.status == Status.OK
    assert result.estimated is True
    assert result.data == {
        "balances": [
            {"currency": "CNY", "amount": "17.1250"},
            {"currency": "USD", "amount": "7.000"},
        ],
        "usage": [
            {"currency": "CNY", "amount": "1.3750"},
            {"currency": "USD", "amount": "0.250"},
        ],
        "adjustments": [],
    }


def test_deepseek_increase_is_adjustment_ambiguity_not_negative_usage() -> None:
    start = deepseek_snapshot("deepseek_end.json", CONTEXT.report_date)
    end = deepseek_snapshot("deepseek_start.json", CONTEXT.delivery_date)

    result = compare_deepseek_snapshots(CONTEXT, OBSERVED_AT, start, end)

    assert result.status == Status.UNAVAILABLE
    assert result.error_code == "balance_adjustment_ambiguity"
    assert result.data["usage"] == []
    assert result.data["adjustments"] == [
        {"currency": "CNY", "amount": "1.3750"},
        {"currency": "USD", "amount": "0.250"},
    ]


def test_deepseek_never_matches_different_currencies() -> None:
    start = deepseek_snapshot("deepseek_start.json", CONTEXT.report_date)
    end = deepseek_snapshot("deepseek_end.json", CONTEXT.delivery_date)
    end.payload["balance_infos"] = end.payload["balance_infos"][:1]

    result = compare_deepseek_snapshots(CONTEXT, OBSERVED_AT, start, end)

    assert result.status == Status.UNAVAILABLE
    assert result.error_code == "currency_set_changed"


@pytest.mark.parametrize(
    ("collector_type", "error_type", "path", "key"),
    [
        (OpenRouterCollector, OpenRouterError, "/api/v1/credits", "openrouter-secret"),
        (DeepSeekCollector, DeepSeekError, "/user/balance", "deepseek-secret"),
    ],
)
def test_http_errors_are_sanitized_and_do_not_expose_credentials(
    collector_type: type[OpenRouterCollector] | type[DeepSeekCollector],
    error_type: type[OpenRouterError] | type[DeepSeekError],
    path: str,
    key: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        return httpx.Response(401, text=f"rejected credential {key}")

    client = httpx.Client(base_url="https://provider.test", transport=httpx.MockTransport(handler))
    instance = collector_type("https://provider.test", key, client)

    with pytest.raises(error_type) as caught:
        instance.snapshot(OBSERVED_AT)

    assert str(caught.value) in {"openrouter_http_error", "deepseek_http_error"}
    assert key not in str(caught.value)


def test_malformed_payload_errors_are_sanitized() -> None:
    client = response_client({"data": {"total_credits": "secret"}}, "/api/v1/credits", "key")
    instance = OpenRouterCollector("https://provider.test", "key", client)

    with pytest.raises(OpenRouterError, match=r"^openrouter_invalid_response$"):
        instance.snapshot(OBSERVED_AT)
