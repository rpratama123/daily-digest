# daily-digest

Daily household and provider digest for Unraid. The service collects independent
sections, stores observations and delivery history in SQLite, renders Telegram
HTML, and schedules Jakarta-time snapshots and reports.

> Telegram delivery is disabled by default. Review a dry run and obtain explicit
> approval before enabling delivery.

## Development

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --extra dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Copy `config.example.yaml` to an ignored `config.yaml`. Configure secret **file
paths**, never secret values. See `docs/secrets.md`.

## CLI

```text
python -m app.main run
python -m app.main collect --date YYYY-MM-DD
python -m app.main render --date YYYY-MM-DD
python -m app.main send --date YYYY-MM-DD [--dry-run]
python -m app.main snapshot
python -m app.main healthcheck
```

`--date` is the digest delivery date in `Asia/Jakarta`; daily measurements refer
to the preceding Jakarta calendar day. Internet availability remains a live
rolling 24-hour measurement.

## Unraid deployment

The Compose service runs as `99:100`, joins external `proxynet`, publishes no
ports, mounts no Docker socket, and uses a read-only root filesystem. Create all
host bind sources before validating Compose:

```text
/mnt/cache/appdata/daily-digest/config.yaml
/mnt/cache/appdata/daily-digest/state/
/mnt/cache/appdata/daily-digest/secrets/
/mnt/user/mediaserver/downloads/
/mnt/user/webdav/daily-digest/vellum/  # only when Vellum is enabled
```

Expected access:

- state: read-write for UID/GID `99:100`;
- configuration, secrets, Rclone logs, and Vellum exports: read-only;
- secrets: narrowly mounted and unavailable to unrelated containers.

Validate and build from the Unraid host without starting the service:

```bash
docker compose config --quiet
docker compose build daily-digest
```

Starting or replacing an existing container and sending a real Telegram message
remain separate operator-approved actions.

## SQLite backup and rollback

Use SQLite's backup facility or stop the service cleanly before copying the
database and its WAL state. Disable delivery before rollback and preserve the
newest delivery ledger. Restoring old delivery state can resend dates that were
already delivered; reconcile those records before restarting.

Unknown Telegram outcomes are intentionally not retried automatically. Use the
documented reconciliation command once implemented rather than editing SQLite.

## Integration status

See `docs/integration-findings.md` and `docs/vellum-export.md`. Expenses, Vellum,
ChatGPT/OpenCode quota, and ESC/POS are disabled by default. Missing collector
data does not suppress healthy sections.

The expense collector connects to `expenses-dashboard:8000` on proxynet and
sends the configured public-domain Host header required by the upstream. This
does not traverse NPM and does not require public-route Basic Auth.
