"""Delivery contracts."""

from __future__ import annotations

from typing import Protocol

from app.models import DeliveryReceipt


class Deliverer(Protocol):
    def deliver(self, message: str) -> DeliveryReceipt: ...
