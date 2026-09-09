"""SQLite persistence with transactional delivery claims."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path

from app.clock import require_aware, utc_now
from app.models import DeliveryState, DigestReport, ProviderSnapshot


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migration = files("app.migrations").joinpath("001_initial.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(migration)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, utc_now().isoformat()),
            )

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def start_run(self, command: str, delivery_date: date | None = None) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO runs(command, delivery_date, started_at) VALUES (?, ?, ?)",
                (
                    command,
                    delivery_date.isoformat() if delivery_date else None,
                    utc_now().isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, outcome: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET finished_at = ?, outcome = ? WHERE id = ?",
                (utc_now().isoformat(), outcome, run_id),
            )

    def save_collector_result(self, run_id: int, result_json: str, name: str, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO collector_results(run_id, name, status, payload_json)
                VALUES (?, ?, ?, ?)""",
                (run_id, name, status, result_json),
            )

    def save_snapshot(self, snapshot: ProviderSnapshot) -> int:
        require_aware(snapshot.observed_at)
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO snapshots(
                    provider, account_key, boundary_date, observed_at, kind, payload_json, canonical
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.provider,
                    snapshot.account_key,
                    snapshot.boundary_date.isoformat() if snapshot.boundary_date else None,
                    snapshot.observed_at.isoformat(),
                    snapshot.kind,
                    json.dumps(snapshot.payload, separators=(",", ":"), sort_keys=True),
                    int(snapshot.canonical),
                ),
            )
            return int(cursor.lastrowid)

    def get_snapshot(
        self, provider: str, boundary_date: date, kind: str, account_key: str = "default"
    ) -> ProviderSnapshot | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM snapshots WHERE provider = ? AND account_key = ?
                AND boundary_date = ? AND kind = ? AND canonical = 1""",
                (provider, account_key, boundary_date.isoformat(), kind),
            ).fetchone()
        if row is None:
            return None
        return ProviderSnapshot(
            provider=row["provider"],
            account_key=row["account_key"],
            boundary_date=date.fromisoformat(row["boundary_date"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            kind=row["kind"],
            payload=json.loads(row["payload_json"]),
            canonical=bool(row["canonical"]),
        )

    def save_digest(self, report: DigestReport, rendered_html: str | None = None) -> int:
        with self.transaction(immediate=True) as connection:
            revision = connection.execute(
                """SELECT COALESCE(MAX(revision), 0) + 1 FROM digests
                WHERE delivery_date = ? AND timezone = ?""",
                (report.delivery_date.isoformat(), report.timezone),
            ).fetchone()[0]
            cursor = connection.execute(
                """INSERT INTO digests(
                    delivery_date, timezone, revision, collected_at, report_json, rendered_html
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    report.delivery_date.isoformat(),
                    report.timezone,
                    revision,
                    report.collected_at.isoformat(),
                    report.model_dump_json(),
                    rendered_html,
                ),
            )
            return int(cursor.lastrowid)

    def get_latest_digest(
        self, delivery_date: date, timezone: str
    ) -> tuple[int, DigestReport, str | None] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT id, report_json, rendered_html FROM digests
                WHERE delivery_date = ? AND timezone = ? ORDER BY revision DESC LIMIT 1""",
                (delivery_date.isoformat(), timezone),
            ).fetchone()
        if row is None:
            return None
        return (
            int(row["id"]),
            DigestReport.model_validate_json(row["report_json"]),
            row["rendered_html"],
        )

    def claim_delivery(
        self,
        digest_id: int,
        delivery_date: date,
        timezone: str,
        channel: str,
        destination_key: str,
        now: datetime,
    ) -> int | None:
        require_aware(now)
        with self.transaction(immediate=True) as connection:
            try:
                cursor = connection.execute(
                    """INSERT INTO deliveries(
                        digest_id, delivery_date, timezone, channel, destination_key,
                        state, claimed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        digest_id,
                        delivery_date.isoformat(),
                        timezone,
                        channel,
                        destination_key,
                        DeliveryState.CLAIMED,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT id, state, updated_at FROM deliveries
                    WHERE delivery_date = ? AND timezone = ? AND channel = ?
                    AND destination_key = ?""",
                    (delivery_date.isoformat(), timezone, channel, destination_key),
                ).fetchone()
                if row is None:
                    return None
                existing_state = DeliveryState(row["state"])
                updated_at = datetime.fromisoformat(row["updated_at"])
                reclaimable = existing_state == DeliveryState.FAILED or (
                    existing_state == DeliveryState.CLAIMED
                    and now - updated_at >= timedelta(minutes=15)
                )
                if not reclaimable:
                    return None
                connection.execute(
                    """UPDATE deliveries SET digest_id = ?, state = ?, claimed_at = ?,
                    updated_at = ?, message_id = NULL WHERE id = ?""",
                    (digest_id, DeliveryState.CLAIMED, now.isoformat(), now.isoformat(), row["id"]),
                )
                return int(row["id"])
            return int(cursor.lastrowid)

    def set_delivery_state(
        self,
        delivery_id: int,
        state: DeliveryState,
        now: datetime,
        message_id: int | None = None,
    ) -> None:
        require_aware(now)
        with self.connect() as connection:
            connection.execute(
                "UPDATE deliveries SET state = ?, updated_at = ?, message_id = ? WHERE id = ?",
                (state, now.isoformat(), message_id, delivery_id),
            )

    def start_delivery_attempt(self, delivery_id: int, now: datetime) -> int:
        require_aware(now)
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO delivery_attempts(delivery_id, started_at) VALUES (?, ?)",
                (delivery_id, now.isoformat()),
            )
            return int(cursor.lastrowid)

    def finish_delivery_attempt(
        self, attempt_id: int, now: datetime, outcome: str, error_code: str | None = None
    ) -> None:
        require_aware(now)
        with self.connect() as connection:
            connection.execute(
                """UPDATE delivery_attempts SET finished_at = ?, outcome = ?, error_code = ?
                WHERE id = ?""",
                (now.isoformat(), outcome, error_code, attempt_id),
            )

    def reconcile_unknown(self, delivery_date: date, timezone: str, state: DeliveryState) -> bool:
        if state not in {DeliveryState.SENT, DeliveryState.FAILED}:
            raise ValueError("unknown deliveries may be reconciled only to sent or failed")
        now = utc_now().isoformat()
        with self.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """UPDATE deliveries SET state = ?, updated_at = ?
                WHERE delivery_date = ? AND timezone = ? AND channel = 'telegram' AND state = ?""",
                (state, now, delivery_date.isoformat(), timezone, DeliveryState.UNKNOWN),
            )
            if cursor.rowcount == 1:
                delivery = connection.execute(
                    """SELECT id FROM deliveries WHERE delivery_date = ? AND timezone = ?
                    AND channel = 'telegram'""",
                    (delivery_date.isoformat(), timezone),
                ).fetchone()
                connection.execute(
                    """INSERT INTO delivery_attempts(
                        delivery_id, started_at, finished_at, outcome
                    ) VALUES (?, ?, ?, ?)""",
                    (delivery["id"], now, now, f"reconciled_{state}"),
                )
            return cursor.rowcount == 1

    def delivery_state(
        self, delivery_date: date, timezone: str, channel: str, destination_key: str
    ) -> DeliveryState | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT state FROM deliveries WHERE delivery_date = ? AND timezone = ?
                AND channel = ? AND destination_key = ?""",
                (delivery_date.isoformat(), timezone, channel, destination_key),
            ).fetchone()
        return DeliveryState(row["state"]) if row else None
