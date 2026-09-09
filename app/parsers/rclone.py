"""Streaming parser for date-blocked Rclone logs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

DATE_PREFIX = re.compile(r"^[0-9]{4}/[0-9]{2}/[0-9]{2}")
TRANSFER_BYTES = re.compile(r"^Transferred:.*(?:B /|B,)")
TRANSFER_PAIR = re.compile(
    r"^Transferred:\s*(?P<sent>[0-9]+(?:\.[0-9]+)?)\s*(?P<sent_unit>[KMGTPE]?i?B)\s*"
    r"/\s*(?P<total>[0-9]+(?:\.[0-9]+)?)\s*(?P<total_unit>[KMGTPE]?i?B)(?:,|$)",
    re.IGNORECASE,
)
ERROR_COUNT = re.compile(r"^Errors:\s*([0-9]+)\b", re.IGNORECASE)
SEVERITY_ERROR = re.compile(r"(?:^|\s)(?:ERROR|CRITICAL|FATAL)\s*:", re.IGNORECASE)
TERMINAL_FAILURE = re.compile(
    r"(?:^|\s)(?:Fatal error|Failed to (?:sync|copy|create file system))\s*:", re.IGNORECASE
)


class RcloneOutcome(StrEnum):
    SUCCESS_NOTHING = "success_nothing"
    SUCCESS_TRANSFERRED = "success_transferred"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    NO_ENTRIES = "no_entries"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RcloneParseResult:
    outcome: RcloneOutcome
    selected_lines: int
    selected_blocks: int
    transferred: str | None = None
    reason: str | None = None


def _transfer_amount(line: str) -> str:
    amount = line.split(",", 1)[0]
    amount = re.sub(r"^Transferred:[ \t]*", "", amount)
    return amount.split(" / ", 1)[0]


def _completed_transfer(line: str) -> bool:
    # Final Rclone summaries may use `-` instead of `100%` for 0 B / 0 B.
    prefix = line.split(",", 1)[0]
    if " / " not in prefix:
        return True
    if re.search(r",\s*100%(?:,|\s|$)", line):
        return True
    pair = TRANSFER_PAIR.match(line)
    if pair is None:
        return False
    try:
        sent = _bytes(pair["sent"], pair["sent_unit"])
        total = _bytes(pair["total"], pair["total_unit"])
    except (InvalidOperation, ValueError):
        return False
    return sent == total


def _bytes(value: str, unit: str) -> Decimal:
    normalized = unit.upper()
    if normalized == "B":
        factor = 1
    elif normalized.endswith("IB"):
        exponent = "KMGTPE".index(normalized[0]) + 1
        factor = 1024**exponent
    else:
        exponent = "KMGTPE".index(normalized[0]) + 1
        factor = 1000**exponent
    return Decimal(value) * factor


def parse_rclone_log(lines: Iterable[str], target_date: date) -> RcloneParseResult:
    target_prefix = target_date.strftime("%Y/%m/%d")
    capture = False
    selected_lines = 0
    selected_blocks = 0
    saw_nothing = False
    saw_failure = False
    failure_reason: str | None = None
    saw_transfer = False
    last_transfer_complete = False
    last_amount: str | None = None

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if DATE_PREFIX.match(line):
            next_capture = line.startswith(target_prefix)
            if next_capture and not capture:
                selected_blocks += 1
            capture = next_capture
        if not capture:
            continue

        selected_lines += 1
        if "There was nothing to transfer" in line:
            saw_nothing = True

        count_match = ERROR_COUNT.match(line)
        if count_match and int(count_match.group(1)) > 0:
            saw_failure = True
            failure_reason = "nonzero_error_count"
        elif SEVERITY_ERROR.search(line):
            saw_failure = True
            failure_reason = "error_line"
        elif TERMINAL_FAILURE.search(line):
            saw_failure = True
            failure_reason = "fatal_error"

        if TRANSFER_BYTES.match(line):
            saw_transfer = True
            last_amount = _transfer_amount(line)
            last_transfer_complete = _completed_transfer(line)

    if selected_lines == 0:
        return RcloneParseResult(RcloneOutcome.NO_ENTRIES, 0, selected_blocks)
    if saw_failure:
        return RcloneParseResult(
            RcloneOutcome.FAILED,
            selected_lines,
            selected_blocks,
            transferred=last_amount,
            reason=failure_reason,
        )
    if saw_transfer and not last_transfer_complete:
        return RcloneParseResult(
            RcloneOutcome.INCOMPLETE,
            selected_lines,
            selected_blocks,
            transferred=last_amount,
            reason="transfer_not_complete",
        )
    if saw_nothing:
        return RcloneParseResult(
            RcloneOutcome.SUCCESS_NOTHING, selected_lines, selected_blocks, transferred="0 B"
        )
    if saw_transfer:
        return RcloneParseResult(
            RcloneOutcome.SUCCESS_TRANSFERRED,
            selected_lines,
            selected_blocks,
            transferred=last_amount,
        )
    return RcloneParseResult(
        RcloneOutcome.UNKNOWN,
        selected_lines,
        selected_blocks,
        reason="completion_not_found",
    )


def parse_rclone_file(path: Path, target_date: date) -> RcloneParseResult:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return parse_rclone_log(handle, target_date)
