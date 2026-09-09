from datetime import date, timedelta

from app.clock import DigestContext


def test_digest_context_uses_previous_jakarta_day() -> None:
    context = DigestContext.create(date(2026, 9, 9))
    assert context.report_date == date(2026, 9, 8)
    assert context.report_end - context.report_start == timedelta(days=1)
    assert context.report_start.utcoffset() == timedelta(hours=7)
