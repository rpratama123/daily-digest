"""Rclone backup collector."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.models import BackupResult, BackupStatus, CollectorResult, Status
from app.parsers.rclone import RcloneOutcome, parse_rclone_file

SIZE = re.compile(r"^(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>[KMGTPE]?i?B)$", re.IGNORECASE)
UNITS = (
    ("PiB", Decimal(1024) ** 5),
    ("TiB", Decimal(1024) ** 4),
    ("GiB", Decimal(1024) ** 3),
    ("MiB", Decimal(1024) ** 2),
    ("KiB", Decimal(1024)),
    ("B", Decimal(1)),
)


def transfer_size_bytes(value: str | None) -> Decimal | None:
    if value is None:
        return None
    match = SIZE.fullmatch(value.strip())
    if match is None:
        return None
    unit = match["unit"]
    normalized = unit.upper()
    try:
        amount = Decimal(match["value"])
        if normalized == "B":
            factor = Decimal(1)
        elif normalized.endswith("IB"):
            factor = Decimal(1024) ** ("KMGTPE".index(normalized[0]) + 1)
        else:
            factor = Decimal(1000) ** ("KMGTPE".index(normalized[0]) + 1)
    except (InvalidOperation, ValueError):
        return None
    return amount * factor


def format_transfer_size(total_bytes: Decimal) -> str:
    for unit, factor in UNITS:
        if total_bytes >= factor or unit == "B":
            amount = total_bytes / factor
            rendered = f"{amount:.2f}".rstrip("0").rstrip(".")
            return f"{rendered} {unit}"
    raise AssertionError("byte unit selection failed")


def inspect_backup(path: Path, report_date: date) -> BackupResult:
    if not path.exists():
        return BackupResult(name=path.name, status=BackupStatus.MISSING, reason="log_missing")
    try:
        parsed = parse_rclone_file(path, report_date)
    except OSError:
        return BackupResult(name=path.name, status=BackupStatus.UNREADABLE, reason="log_unreadable")

    if parsed.outcome in {RcloneOutcome.SUCCESS_NOTHING, RcloneOutcome.SUCCESS_TRANSFERRED}:
        status = BackupStatus.OK
    elif parsed.outcome == RcloneOutcome.NO_ENTRIES:
        status = BackupStatus.NO_ENTRIES
    elif parsed.outcome in {RcloneOutcome.INCOMPLETE, RcloneOutcome.UNKNOWN}:
        status = BackupStatus.INCOMPLETE
    else:
        status = BackupStatus.FAILED
    return BackupResult(
        name=path.name,
        status=status,
        transferred=parsed.transferred,
        reason=parsed.reason,
        selected_lines=parsed.selected_lines,
        selected_blocks=parsed.selected_blocks,
    )


def collect_backups(
    directory: Path,
    log_names: list[str],
    report_date: date,
    observed_at: datetime,
    period_start: datetime,
    period_end: datetime,
) -> CollectorResult:
    results = [inspect_backup(directory / name, report_date) for name in log_names]
    ok_count = sum(item.status == BackupStatus.OK for item in results)
    confirmed_sizes = [
        size
        for item in results
        if item.status == BackupStatus.OK
        and (size := transfer_size_bytes(item.transferred)) is not None
    ]
    transferred_bytes = sum(confirmed_sizes, Decimal(0))
    status = Status.OK if ok_count == len(results) else Status.FAILED
    return CollectorResult(
        name="backups",
        status=status,
        observed_at=observed_at,
        period_start=period_start,
        period_end=period_end,
        data={
            "ok_count": ok_count,
            "total_count": len(results),
            "transferred_bytes": format(transferred_bytes, "f"),
            "transferred_total": format_transfer_size(transferred_bytes),
            "results": [item.model_dump(mode="json") for item in results],
        },
        error_code=None if status == Status.OK else "backup_failures",
    )
