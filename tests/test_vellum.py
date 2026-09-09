import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.clock import DigestContext
from app.collectors.vellum import VellumCollector
from app.imports.vellum import VellumExportDocument, VellumImportError, import_vellum_exports
from app.models import Status

FIXTURES = Path(__file__).parent / "fixtures" / "vellum"
CONTEXT = DigestContext.create(date(2026, 9, 11))
NOW = datetime(2026, 9, 11, 7, tzinfo=CONTEXT.timezone)


class Source:
    def __init__(self, documents: dict[date, VellumExportDocument]) -> None:
        self.documents = documents
        self.calls = 0

    def read_export(self, snapshot_date: date) -> VellumExportDocument | None:
        self.calls += 1
        return self.documents.get(snapshot_date)


def document(snapshot_date: date, fixture: str | None = None) -> VellumExportDocument:
    name = fixture or f"{snapshot_date.isoformat()}.json"
    return VellumExportDocument(
        name=f"{snapshot_date.isoformat()}.json",
        content=(FIXTURES / name).read_bytes(),
        modified_at=datetime(2026, 9, 11, tzinfo=UTC),
    )


def valid_source() -> Source:
    return Source(
        {
            date(2026, 9, 10): document(date(2026, 9, 10)),
            date(2026, 9, 11): document(date(2026, 9, 11)),
        }
    )


def test_consecutive_midnight_balances_produce_estimated_usage() -> None:
    result = VellumCollector(valid_source(), enabled=True).collect(CONTEXT, NOW)
    assert result.status == Status.OK
    assert result.estimated is True
    assert result.data == {
        "currency": "USD",
        "balance": "5.81",
        "pending": "0",
        "settled": "5.81",
        "source": "remaining_balance_delta",
        "usage": "0.42",
    }


@pytest.mark.parametrize(
    ("documents", "code"),
    [
        ({date(2026, 9, 11): document(date(2026, 9, 11))}, "missing_start_snapshot"),
        ({date(2026, 9, 10): document(date(2026, 9, 10))}, "missing_end_snapshot"),
    ],
)
def test_missing_boundaries_are_explicit(
    documents: dict[date, VellumExportDocument], code: str
) -> None:
    result = VellumCollector(Source(documents), enabled=True).collect(CONTEXT, NOW)
    assert result.status == Status.UNAVAILABLE
    assert result.error_code == code
    if code == "missing_start_snapshot":
        assert result.data["balance"] == "5.81"


def test_manual_first_snapshot_is_not_misrepresented_as_midnight() -> None:
    context = DigestContext.create(date(2026, 9, 10))
    source = Source(
        {
            date(2026, 9, 9): document(date(2026, 9, 9), "manual-2026-09-09.json"),
            date(2026, 9, 10): document(date(2026, 9, 10)),
        }
    )
    result = VellumCollector(source, enabled=True).collect(
        context, datetime(2026, 9, 10, 7, tzinfo=context.timezone)
    )
    assert result.status == Status.UNAVAILABLE
    assert result.error_code == "vellum_snapshot_outside_midnight_tolerance"
    assert result.data["balance"] == "6.23"


def test_balance_increase_is_adjustment_ambiguity() -> None:
    raw = json.loads((FIXTURES / "2026-09-11.json").read_text(encoding="utf-8"))
    raw["credits"]["remaining"] = 7.00
    end = document(date(2026, 9, 11)).model_copy(update={"content": json.dumps(raw).encode()})
    source = Source(
        {
            date(2026, 9, 10): document(date(2026, 9, 10)),
            date(2026, 9, 11): end,
        }
    )
    result = VellumCollector(source, enabled=True).collect(CONTEXT, NOW)
    assert result.status == Status.UNAVAILABLE
    assert result.error_code == "balance_adjustment_ambiguity"
    assert result.data["balance"] == "7.0"


def test_stale_flag_invalid_schema_and_oversized_export_are_rejected() -> None:
    raw = json.loads((FIXTURES / "2026-09-11.json").read_text(encoding="utf-8"))
    raw["credits"]["stale"] = True
    stale = document(date(2026, 9, 11)).model_copy(update={"content": json.dumps(raw).encode()})
    source = Source({date(2026, 9, 10): document(date(2026, 9, 10)), date(2026, 9, 11): stale})
    with pytest.raises(VellumImportError, match="vellum_credits_stale"):
        import_vellum_exports(source, CONTEXT, NOW)

    raw["credits"]["stale"] = False
    raw["schema_version"] = 2
    invalid = stale.model_copy(update={"content": json.dumps(raw).encode()})
    source.documents[date(2026, 9, 11)] = invalid
    with pytest.raises(VellumImportError, match="vellum_export_invalid"):
        import_vellum_exports(source, CONTEXT, NOW)

    source.documents[date(2026, 9, 11)] = document(date(2026, 9, 11))
    with pytest.raises(VellumImportError, match="vellum_export_oversized"):
        import_vellum_exports(source, CONTEXT, NOW, max_file_bytes=10)


def test_latest_file_is_not_used_for_historical_boundaries(tmp_path: Path) -> None:
    (tmp_path / "latest.json").write_bytes((FIXTURES / "2026-09-11.json").read_bytes())
    from app.imports.vellum import DirectoryVellumSource

    assert DirectoryVellumSource(tmp_path).read_export(date(2026, 9, 11)) is None


def test_disabled_vellum_does_not_read_source() -> None:
    source = valid_source()
    result = VellumCollector(source).collect(CONTEXT, NOW)
    assert result.status == Status.DISABLED
    assert source.calls == 0


def test_old_end_snapshot_is_stale() -> None:
    with pytest.raises(VellumImportError, match="vellum_export_stale"):
        import_vellum_exports(valid_source(), CONTEXT, NOW + timedelta(days=2))
