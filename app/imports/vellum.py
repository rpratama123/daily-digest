"""Validated reader for verified Vellum platform-credit exports."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.clock import DigestContext


class VellumImportError(ValueError):
    def __init__(self, code: str, data: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.data = data or {}


class VellumExportDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    content: bytes
    modified_at: datetime

    @field_validator("modified_at")
    @classmethod
    def modified_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("modified_at must be timezone-aware")
        return value


class VellumSource(Protocol):
    """Read-only source for immutable date-named exports."""

    def read_export(self, snapshot_date: date) -> VellumExportDocument | None: ...


class DirectoryVellumSource:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def read_export(self, snapshot_date: date) -> VellumExportDocument | None:
        if not self.directory.is_dir():
            raise VellumImportError("vellum_export_directory_missing")
        path = self.directory / f"{snapshot_date.isoformat()}.json"
        if not path.is_file():
            return None
        stat = path.stat()
        return VellumExportDocument(
            name=path.name,
            content=path.read_bytes(),
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        )


class _ExportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VellumCredits(_ExportModel):
    as_of: datetime
    pending: Decimal
    remaining: Decimal
    settled: Decimal
    stale: bool
    unit: Literal["USD"]

    @field_validator("as_of")
    @classmethod
    def as_of_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("credits.as_of must be timezone-aware")
        return value

    @field_validator("pending", "remaining", "settled")
    @classmethod
    def amount_is_valid(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("credit amount must be finite and nonnegative")
        return value


class VellumCreditSnapshot(_ExportModel):
    captured_at: datetime
    credits: VellumCredits
    schema_version: Literal[1]
    snapshot_date: date
    source: Literal["vellum_platform_credits"]
    timezone: Literal["Asia/Jakarta"]

    @field_validator("captured_at")
    @classmethod
    def captured_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value


def _load_snapshot(
    source: VellumSource,
    snapshot_date: date,
    context: DigestContext,
    *,
    max_file_bytes: int,
    midnight_tolerance: timedelta,
) -> VellumCreditSnapshot | None:
    document = source.read_export(snapshot_date)
    if document is None:
        return None
    if document.name != f"{snapshot_date.isoformat()}.json":
        raise VellumImportError("vellum_filename_mismatch")
    if len(document.content) > max_file_bytes:
        raise VellumImportError("vellum_export_oversized")
    try:
        raw = json.loads(document.content, parse_float=Decimal, parse_int=Decimal)
        snapshot = VellumCreditSnapshot.model_validate(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise VellumImportError("vellum_export_invalid") from exc
    if snapshot.snapshot_date != snapshot_date:
        raise VellumImportError("vellum_snapshot_date_mismatch")

    expected = datetime.combine(snapshot_date, time.min, context.timezone)
    captured = snapshot.captured_at.astimezone(context.timezone)
    if abs(captured - expected) > midnight_tolerance:
        raise VellumImportError("vellum_snapshot_outside_midnight_tolerance")
    if abs(snapshot.credits.as_of - snapshot.captured_at) > timedelta(minutes=10):
        raise VellumImportError("vellum_credit_timestamp_mismatch")
    if snapshot.credits.stale:
        raise VellumImportError("vellum_credits_stale")
    return snapshot


def import_vellum_exports(
    source: VellumSource,
    context: DigestContext,
    observed_at: datetime,
    *,
    max_file_bytes: int = 131_072,
    max_age: timedelta = timedelta(hours=36),
    midnight_tolerance: timedelta = timedelta(seconds=120),
) -> dict[str, object]:
    """Infer estimated usage from consecutive midnight remaining-credit snapshots."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise VellumImportError("vellum_naive_observed_at")

    start_error: VellumImportError | None = None
    try:
        start = _load_snapshot(
            source,
            context.report_date,
            context,
            max_file_bytes=max_file_bytes,
            midnight_tolerance=midnight_tolerance,
        )
    except VellumImportError as exc:
        start = None
        start_error = exc
    end = _load_snapshot(
        source,
        context.delivery_date,
        context,
        max_file_bytes=max_file_bytes,
        midnight_tolerance=midnight_tolerance,
    )
    if end is None:
        raise VellumImportError("missing_end_snapshot")
    if end.captured_at > observed_at or observed_at - end.captured_at > max_age:
        raise VellumImportError("vellum_export_stale")

    data = {
        "currency": "USD",
        "balance": format(end.credits.remaining, "f"),
        "pending": format(end.credits.pending, "f"),
        "settled": format(end.credits.settled, "f"),
        "source": "remaining_balance_delta",
    }
    if start_error is not None:
        raise VellumImportError(start_error.code, data) from start_error
    if start is None:
        raise VellumImportError("missing_start_snapshot", data)

    usage = start.credits.remaining - end.credits.remaining
    if usage < 0:
        data["adjustment"] = format(-usage, "f")
        raise VellumImportError("balance_adjustment_ambiguity", data)
    data["usage"] = format(usage, "f")
    return data
