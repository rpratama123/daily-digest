"""Safe application logging defaults."""

from __future__ import annotations

import logging
import re

SENSITIVE = re.compile(r"(?i)(authorization|api[_-]?key|password|token|cookie)(\s*[:=]\s*)(\S+)")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = SENSITIVE.sub(r"\1\2[REDACTED]", message)
        record.args = ()
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # HTTP request URLs may contain credentials (notably Telegram bot tokens).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
