"""Future ESC/POS delivery interface; deliberately disabled."""

from __future__ import annotations


class EscPosDeliverer:
    """Reserved for a future thermal printer with partial-cut support."""

    enabled = False
    supports_partial_cut = True

    def deliver(self, message: str) -> None:
        raise RuntimeError("ESC/POS delivery is not implemented")
