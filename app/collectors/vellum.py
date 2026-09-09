"""Collector wrapper around the transport-neutral Vellum importer."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.clock import DigestContext
from app.imports.vellum import VellumImportError, VellumSource, import_vellum_exports
from app.models import CollectorResult, Status


class VellumCollector:
    def __init__(
        self,
        source: VellumSource,
        *,
        enabled: bool = False,
        max_file_bytes: int = 131_072,
        max_age: timedelta = timedelta(hours=36),
        midnight_tolerance: timedelta = timedelta(seconds=120),
    ) -> None:
        self.source = source
        self.enabled = enabled
        self.max_file_bytes = max_file_bytes
        self.max_age = max_age
        self.midnight_tolerance = midnight_tolerance

    def collect(self, context: DigestContext, observed_at: datetime) -> CollectorResult:
        if not self.enabled:
            return CollectorResult(
                name="vellum",
                status=Status.DISABLED,
                observed_at=observed_at,
                period_start=context.report_start,
                period_end=context.report_end,
            )
        try:
            data = import_vellum_exports(
                self.source,
                context,
                observed_at,
                max_file_bytes=self.max_file_bytes,
                max_age=self.max_age,
                midnight_tolerance=self.midnight_tolerance,
            )
        except (OSError, VellumImportError) as exc:
            code = exc.code if isinstance(exc, VellumImportError) else "vellum_source_unavailable"
            status = (
                Status.STALE
                if code in {"vellum_export_stale", "vellum_credits_stale"}
                else Status.UNAVAILABLE
            )
            return CollectorResult(
                name="vellum",
                status=status,
                observed_at=observed_at,
                period_start=context.report_start,
                period_end=context.report_end,
                data=exc.data if isinstance(exc, VellumImportError) else {},
                error_code=code,
            )
        return CollectorResult(
            name="vellum",
            status=Status.OK,
            observed_at=observed_at,
            period_start=context.report_start,
            period_end=context.report_end,
            estimated=True,
            data=data,
        )
