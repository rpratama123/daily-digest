import logging

from app.logging import RedactingFilter, configure_logging


def test_redacting_filter_removes_key_value_secrets() -> None:
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "token=do-not-log password: hidden", (), None
    )
    assert RedactingFilter().filter(record)
    assert "do-not-log" not in record.getMessage()
    assert "hidden" not in record.getMessage()


def test_http_request_logging_is_suppressed() -> None:
    configure_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
