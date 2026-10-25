# AGENTS.md

Python 3.12 service (`uv`-managed, SQLite-backed) that schedules daily household/provider digests, renders Telegram HTML, and runs on Unraid. Single package (`app/`).

## Commands

```bash
uv sync --locked --extra dev        # required before anything works
uv run pytest -q                    # all tests; they are offline and use tests/fixtures/
uv run pytest tests/test_config.py -q     # single test file
uv run python -m app.main send --date YYYY-MM-DD --dry-run --fixture  # end-to-end preview, no network
uv run ruff check . && uv run ruff format --check .   # lint/format; run both before declaring done
```

`python -m app.main` is the container ENTRYPOINT; subcommands: `run collect render send snapshot healthcheck reconcile`.

## Configuration gotchas

- Config is pydantic-settings: YAML file (path from `DAILY_DIGEST_CONFIG`, default `config.yaml`, gitignored — copy `config.example.yaml`) layered under env vars with prefix `DAILY_DIGEST_` and `__` nesting.
- Every settings model uses `extra="forbid"`: an unknown YAML key or env var fails startup with a validation error, not silently.
- Secrets are file paths only (`*_FILE` pointing under `/run/secrets/daily-digest/`), never values in YAML/env/git. Policy and layout: `docs/secrets.md`.
- Enabling a collector while its secret file doesn't exist raises at config load ("file is unavailable"). On a dev machine use `--fixture` mode or the shipped defaults (expenses, Vellum, Codex, ESC/POS, Telegram are disabled by default).
- Local `collect`/`snapshot` outside the container hits real endpoints (e.g. Home Assistant at `10.20.30.55:8123`). Don't run them casually.

## Domain rules that are easy to break

- All scheduling and `--date` interpretation uses `Asia/Jakarta`. `--date` is the digest *delivery* date; daily measurements cover the *preceding* Jakarta day. Internet uptime is a rolling 24h live window, not calendar-day.
- Telegram delivery is disabled by default by design: review a dry run and get explicit operator approval before enabling. Unknown Telegram send outcomes are resolved only via `reconcile --date --state sent|failed` — never hand-edit the SQLite delivery ledger (restoring old state can resend dates).
- Missing collector data suppresses only that section, never the whole digest.

## Deployment / container contract

- Target runtime is UID/GID `99:100` (Unraid nobody/users). Dockerfile and compose both pin it; `tzdata`, `TZ=Asia/Jakarta`, read-only filesystem, no published ports, external `proxynet`, no Docker socket.
- `tests/test_deployment.py` asserts these invariants from `compose.yaml`/`.dockerignore` (user, `read_only`, `cap_drop: [ALL]`, no `ports`, no `docker.sock`, read-only mounts for secrets/logs/exports). Editing deployment files without updating that test fails CI-less test runs.
- `compose.yaml` is Unraid-shaped (bind paths under `/mnt/cache/appdata`, `/mnt/user`) and used for local build/config validation (`docker compose config --quiet && docker compose build`). Real deployments must run from prebuilt images. GitHub Actions publishes `ghcr.io/<owner>/daily-digest` (`.github/workflows/publish.yml`, tags `latest` on main, semver on `v*` tags, `sha-*` on every build; lint+test job gates the push). Compose is not how the app is deployed on Unraid.
- Wheel build excludes `tests/`, `docs/`, `samples/` (hatch config); dashboards don't belong in `app/` runtime deps.
- Helpers in `scripts/` (`codex_quota_export.py`, `unraid_codex_quota_export.sh`) run manually on the Unraid host to refresh Codex export data — not part of the service scheduler.

## Notes

- No pre-commit. CI (`.github/workflows/publish.yml`) runs the same lint/format/test loop on push; the four commands at the top are the local verification loop.
- Integration research lives in `docs/integration-findings.md` (and vellum/codex export docs); consult before changing collector behavior.
