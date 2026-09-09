"""Telegram private-chat delivery without automatic ambiguous retries."""

from __future__ import annotations

from datetime import datetime

import httpx

from app.models import DeliveryReceipt, DeliveryState


class TelegramDefinitiveError(RuntimeError):
    """A Telegram rejection that is known not to have delivered the message."""


class TelegramUnknownError(RuntimeError):
    """A transport outcome that may have delivered the message."""


class TelegramDeliverer:
    def __init__(
        self,
        token: str,
        chat_id: str,
        observed_at: datetime,
        client: httpx.Client | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.observed_at = observed_at
        self._owned_client = client is None
        self.client = client or httpx.Client(base_url=f"https://api.telegram.org/bot{token}")

    def close(self) -> None:
        if self._owned_client:
            self.client.close()

    def validate_private_chat(self) -> None:
        try:
            response = self.client.post("/getChat", json={"chat_id": self.chat_id})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramDefinitiveError("telegram_destination_validation_failed") from None
        if not payload.get("ok") or payload.get("result", {}).get("type") != "private":
            raise TelegramDefinitiveError("telegram_destination_not_private")

    def deliver(self, message: str) -> DeliveryReceipt:
        try:
            response = self.client.post(
                "/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.TransportError:
            raise TelegramUnknownError("telegram_delivery_unknown") from None
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPStatusError, ValueError):
            raise TelegramDefinitiveError("telegram_delivery_rejected") from None
        if not payload.get("ok") or not isinstance(
            payload.get("result", {}).get("message_id"), int
        ):
            raise TelegramDefinitiveError("telegram_invalid_response")
        return DeliveryReceipt(
            state=DeliveryState.SENT,
            observed_at=self.observed_at,
            message_id=payload["result"]["message_id"],
        )
