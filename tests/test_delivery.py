from datetime import UTC, datetime

import httpx
import pytest

from app.deliverers.telegram import (
    TelegramDefinitiveError,
    TelegramDeliverer,
    TelegramUnknownError,
)
from app.models import DeliveryState

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def test_private_chat_validation_and_send() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/getChat":
            return httpx.Response(200, json={"ok": True, "result": {"type": "private"}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    client = httpx.Client(base_url="https://telegram.test", transport=httpx.MockTransport(handler))
    deliverer = TelegramDeliverer("fake", "123", NOW, client)
    deliverer.validate_private_chat()
    receipt = deliverer.deliver("safe <b>message</b>")
    assert receipt.state == DeliveryState.SENT
    assert receipt.message_id == 42
    assert calls == ["/getChat", "/sendMessage"]


def test_non_private_chat_is_definitively_rejected() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True, "result": {"type": "group"}})
    )
    deliverer = TelegramDeliverer(
        "fake", "123", NOW, httpx.Client(base_url="https://telegram.test", transport=transport)
    )
    with pytest.raises(TelegramDefinitiveError):
        deliverer.validate_private_chat()


def test_transport_failure_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    deliverer = TelegramDeliverer(
        "fake",
        "123",
        NOW,
        httpx.Client(base_url="https://telegram.test", transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(TelegramUnknownError):
        deliverer.deliver("message")
