CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    command TEXT NOT NULL,
    delivery_date TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT
);

CREATE TABLE IF NOT EXISTS collector_results (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    account_key TEXT NOT NULL,
    boundary_date TEXT,
    observed_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    canonical INTEGER NOT NULL CHECK (canonical IN (0, 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS snapshots_canonical_slot
ON snapshots(provider, account_key, boundary_date, kind)
WHERE canonical = 1;

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY,
    delivery_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    revision INTEGER NOT NULL,
    collected_at TEXT NOT NULL,
    report_json TEXT NOT NULL,
    rendered_html TEXT,
    UNIQUE(delivery_date, timezone, revision)
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY,
    digest_id INTEGER NOT NULL REFERENCES digests(id),
    delivery_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    channel TEXT NOT NULL,
    destination_key TEXT NOT NULL,
    state TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    message_id INTEGER,
    UNIQUE(delivery_date, timezone, channel, destination_key)
);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    id INTEGER PRIMARY KEY,
    delivery_id INTEGER NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT,
    error_code TEXT
);
