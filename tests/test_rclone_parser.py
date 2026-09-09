from datetime import date
from pathlib import Path

import pytest

from app.parsers.rclone import RcloneOutcome, parse_rclone_file, parse_rclone_log

FIXTURES = Path(__file__).parent / "fixtures" / "rclone"
REPORT_DATE = date(2026, 9, 5)


@pytest.mark.parametrize(
    ("fixture", "outcome", "amount"),
    [
        ("success.log", RcloneOutcome.SUCCESS_TRANSFERRED, "42.407 MiB"),
        ("nothing.log", RcloneOutcome.SUCCESS_NOTHING, "0 B"),
        ("failure_override.log", RcloneOutcome.FAILED, "12 MiB"),
    ],
)
def test_fixture_outcomes(fixture: str, outcome: RcloneOutcome, amount: str) -> None:
    parsed = parse_rclone_file(FIXTURES / fixture, REPORT_DATE)
    assert parsed.outcome == outcome
    assert parsed.transferred == amount


def test_undated_preamble_is_ignored_and_target_blocks_can_repeat() -> None:
    lines = [
        "Transferred: 9 MiB, 100%\n",
        "2026/09/05 first\n",
        "There was nothing to transfer\n",
        "2026/09/06 other\n",
        "2026/09/05 retry\n",
        "Transferred: 2 MiB, 100%\n",
    ]
    parsed = parse_rclone_log(lines, REPORT_DATE)
    assert parsed.selected_blocks == 2
    assert parsed.selected_lines == 4
    assert parsed.outcome == RcloneOutcome.SUCCESS_NOTHING


def test_failure_outside_selected_date_is_ignored() -> None:
    parsed = parse_rclone_log(
        [
            "2026/09/05 start\n",
            "Transferred: 2 MiB / 2 MiB, 100%\n",
            "2026/09/06 ERROR : failed\n",
        ],
        REPORT_DATE,
    )
    assert parsed.outcome == RcloneOutcome.SUCCESS_TRANSFERRED


@pytest.mark.parametrize(
    "line",
    [
        "Errors: 2\n",
        "2026/09/05 10:00:00 ERROR : object failed\n",
        "Fatal error: failed to start\n",
        "Failed to sync: permission denied\n",
    ],
)
def test_failure_evidence_overrides_success(line: str) -> None:
    parsed = parse_rclone_log(
        ["2026/09/05 start\n", "There was nothing to transfer\n", line], REPORT_DATE
    )
    assert parsed.outcome == RcloneOutcome.FAILED


def test_zero_errors_do_not_override_success() -> None:
    parsed = parse_rclone_log(
        ["2026/09/05 start\r\n", "Errors: 0\r\n", "Transferred: 1 GiB, 1 GiB/s\r\n"],
        REPORT_DATE,
    )
    assert parsed.outcome == RcloneOutcome.SUCCESS_TRANSFERRED


@pytest.mark.parametrize(
    "summary",
    [
        "Transferred: 0 B / 0 B, -, 0 B/s, ETA -\n",
        "Transferred: 1 GiB / 1024 MiB, -, 0 B/s, ETA -\n",
    ],
)
def test_equal_amount_summary_is_complete_without_100_percent(summary: str) -> None:
    parsed = parse_rclone_log(["2026/09/05 start\n", summary], REPORT_DATE)
    assert parsed.outcome == RcloneOutcome.SUCCESS_TRANSFERRED


def test_partial_transfer_is_incomplete() -> None:
    parsed = parse_rclone_log(
        ["2026/09/05 start\n", "Transferred: 1 MiB / 2 MiB, 50%, ETA 1m\n"], REPORT_DATE
    )
    assert parsed.outcome == RcloneOutcome.INCOMPLETE


def test_last_incomplete_summary_overrides_earlier_apparent_success() -> None:
    parsed = parse_rclone_log(
        [
            "2026/09/05 start\n",
            "There was nothing to transfer\n",
            "Transferred: 2 MiB / 2 MiB, 100%\n",
            "Transferred: 1 MiB / 2 MiB, 50%\n",
        ],
        REPORT_DATE,
    )
    assert parsed.outcome == RcloneOutcome.INCOMPLETE


def test_count_summary_is_not_a_byte_completion() -> None:
    parsed = parse_rclone_log(["2026/09/05 start\n", "Transferred: 3 / 3, 100%\n"], REPORT_DATE)
    assert parsed.outcome == RcloneOutcome.UNKNOWN


def test_empty_and_missing_date_have_no_entries() -> None:
    assert parse_rclone_log([], REPORT_DATE).outcome == RcloneOutcome.NO_ENTRIES
    assert parse_rclone_log(["2026/09/04 other\n"], REPORT_DATE).outcome == RcloneOutcome.NO_ENTRIES


def test_leading_space_date_preserves_legacy_lexical_behavior() -> None:
    parsed = parse_rclone_log(
        [
            "2026/09/05 start\n",
            " 2026/09/06 not-a-boundary\n",
            "There was nothing to transfer\n",
        ],
        REPORT_DATE,
    )
    assert parsed.outcome == RcloneOutcome.SUCCESS_NOTHING


def test_large_input_is_consumed_as_an_iterable() -> None:
    def lines():
        yield "2026/09/05 start\n"
        for _ in range(20_000):
            yield "routine detail\n"
        yield "Transferred: 1 MiB, 100%\n"

    parsed = parse_rclone_log(lines(), REPORT_DATE)
    assert parsed.selected_lines == 20_002
    assert parsed.outcome == RcloneOutcome.SUCCESS_TRANSFERRED
