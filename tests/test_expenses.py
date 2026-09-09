import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from app.clock import DigestContext
from app.collectors.expenses import ExpenseCollector
from app.models import Status

FIXTURE = Path(__file__).parent / "fixtures" / "expenses" / "daily.json"
NOW = datetime(2026, 9, 9, 2, tzinfo=UTC)
CONTEXT = DigestContext.create(date(2026, 9, 9))


def test_expense_request_and_confirmed_schema() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/digest/daily"
        assert dict(request.url.params) == {
            "date": "2026-09-08",
            "timezone": "Asia/Jakarta",
        }
        assert "Authorization" not in request.headers
        assert request.headers["Host"] == "expenses.cikohome.stream"
        return httpx.Response(200, json=payload)

    client = httpx.Client(base_url="http://expenses.test", transport=httpx.MockTransport(handler))
    result = ExpenseCollector(
        "http://expenses.test",
        enabled=True,
        host_header="expenses.cikohome.stream",
        client=client,
    ).collect(CONTEXT, NOW)
    assert result.status == Status.OK
    assert result.data["total"] == 805630


@pytest.mark.parametrize("value", [True, 1.5, -1])
def test_expense_money_is_a_strict_nonnegative_integer(value: object) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["total"] = value
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    client = httpx.Client(base_url="http://expenses.test", transport=transport)
    result = ExpenseCollector("http://expenses.test", enabled=True, client=client).collect(
        CONTEXT, NOW
    )
    assert result.status == Status.UNAVAILABLE


def test_expense_basic_auth_requires_and_uses_pair() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json=payload)

    client = httpx.Client(base_url="http://expenses.test", transport=httpx.MockTransport(handler))
    collector = ExpenseCollector(
        "http://expenses.test", enabled=True, username="user", password="pass", client=client
    )
    assert collector.collect(CONTEXT, NOW).status == Status.OK
    with pytest.raises(ValueError):
        ExpenseCollector("http://expenses.test", username="user")


def test_disabled_expenses_make_no_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = httpx.Client(base_url="http://expenses.test", transport=httpx.MockTransport(handler))
    result = ExpenseCollector("http://expenses.test", client=client).collect(CONTEXT, NOW)
    assert result.status == Status.DISABLED
    assert calls == 0
