#!/usr/bin/env python3
"""Export sanitized Codex weekly quota via the official app-server protocol."""

from __future__ import annotations

import json
import math
import os
import select
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

SCHEMA_VERSION = 1
SOURCE = "codex_app_server_rate_limits"
WEEK_MINUTES = 7 * 24 * 60


class ExportError(RuntimeError):
    """Sanitized exporter failure."""


def _percent(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ExportError("invalid_percent")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ExportError("invalid_percent") from exc
    if not result.is_finite() or not Decimal(0) <= result <= Decimal(100):
        raise ExportError("invalid_percent")
    return result


def _window(bucket: dict[str, Any]) -> dict[str, Any]:
    window = bucket.get("secondary")
    if not isinstance(window, dict):
        raise ExportError("missing_weekly_window")
    used = _percent(window.get("usedPercent"))
    minutes = window.get("windowDurationMins")
    resets_at = window.get("resetsAt")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes != WEEK_MINUTES:
        raise ExportError("unexpected_weekly_window")
    if isinstance(resets_at, bool) or not isinstance(resets_at, (int, float)):
        raise ExportError("invalid_reset_time")
    if not math.isfinite(resets_at) or resets_at <= 0:
        raise ExportError("invalid_reset_time")
    reset = datetime.fromtimestamp(resets_at, UTC)
    remaining = Decimal(100) - used
    return {
        "used_percent": format(used, "f"),
        "remaining_percent": format(remaining, "f"),
        "window_minutes": minutes,
        "resets_at": reset.isoformat().replace("+00:00", "Z"),
    }


def sanitized_export(result: dict[str, Any], captured_at: datetime) -> dict[str, Any]:
    """Extract only non-identifying weekly quota fields from app-server output."""
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ExportError("naive_capture_time")
    by_id = result.get("rateLimitsByLimitId")
    bucket = by_id.get("codex") if isinstance(by_id, dict) else None
    if not isinstance(bucket, dict):
        candidate = result.get("rateLimits")
        if isinstance(candidate, dict) and candidate.get("limitId") in {None, "codex"}:
            bucket = candidate
    if not isinstance(bucket, dict):
        raise ExportError("missing_codex_bucket")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "captured_at": captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "bucket_id": "codex",
        "weekly": _window(bucket),
    }


def fetch_rate_limits(timeout_seconds: float = 15.0) -> dict[str, Any]:
    """Call app-server without starting a thread or model turn."""
    codex_binary = os.environ.get("CODEX_BINARY", "codex")
    process = subprocess.Popen(
        [codex_binary, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise ExportError("app_server_start_failed")
    requests = [
        {
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "daily_digest_exporter",
                    "title": "Daily Digest Exporter",
                    "version": "1.0.0",
                }
            },
        },
        {"method": "initialized", "params": {}},
        {"method": "account/rateLimits/read", "id": 2, "params": {}},
    ]
    try:
        for request in requests:
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], 0.5)
            if not ready:
                if process.poll() is not None:
                    raise ExportError("app_server_exited")
                continue
            line = process.stdout.readline()
            if not line:
                raise ExportError("app_server_exited")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") != 2:
                continue
            if "error" in response:
                raise ExportError("rate_limit_read_failed")
            result = response.get("result")
            if not isinstance(result, dict):
                raise ExportError("invalid_rate_limit_response")
            return result
        raise ExportError("app_server_timeout")
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    try:
        payload = sanitized_export(fetch_rate_limits(), datetime.now(UTC))
    except (ExportError, OSError) as exc:
        code = str(exc) if isinstance(exc, ExportError) else "app_server_unavailable"
        print(f"Codex quota export failed: {code}", file=sys.stderr)
        return 1
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
