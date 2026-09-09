# Daily Digest Implementation Plan

Status: approved on 2026-09-09

## Objective

Build a production-quality Python service named `daily-digest` for Unraid. It
collects household and provider data, persists observations and run history in
SQLite, renders a concise daily report, and can deliver that report to a private
Telegram chat. A future ESC/POS printer deliverer is represented by a disabled
stub only.

No real Telegram delivery or existing-container change is permitted without a
separate, explicit approval.

## Confirmed environment and policy

- Time zone: `Asia/Jakarta`.
- Unraid source: `/mnt/cache/appdata/openvscode-server/workspace/daily-digest`.
- Persistent application root: `/mnt/cache/appdata/daily-digest`.
- Container UID/GID: `99:100`.
- External Docker network: `proxynet`.
- Home Assistant uses host networking and is reachable at
  `http://10.20.30.55:8123` or `https://ha.cikohome.stream`.
- Expense dashboard is expected at `http://expenses-dashboard:8000` on
  `proxynet`; its public NPM route uses HTTP Basic authentication, while the
  internal route is expected to bypass it and must be verified.
- Rclone logs are mounted read-only at `/data/rclone-logs` from
  `/mnt/user/mediaserver/downloads`.
- Vellum exports are expected under the dedicated WebDAV-backed host directory
  `/mnt/user/webdav/daily-digest/vellum`, mounted read-only in the application.
- Development secrets are mounted under `/run/secrets/daily-digest`. Only file
  paths may be configured or discussed; secret values must never be printed,
  logged, committed, placed in fixtures, or passed on a command line.
- Do not mount or use the Docker socket. Do not inspect the expense database.

## Date semantics

- A CLI `--date` is the Jakarta **digest delivery date**.
- Energy, backups, expenses, and AI usage refer to the preceding Jakarta
  calendar day.
- Internet availability is the source entity's rolling 24-hour value and must
  be labeled `last 24h`, never `yesterday`.
- The digest runs at 07:00 Jakarta time.
- Provider boundary snapshots run at 00:00 Jakarta time.
- Startup catch-up is eligible only at or after 07:00 for the current Jakarta
  date, and only if that date has no completed or unresolved delivery record.
- Catch-up does not process a historical backlog.
- Every datetime is timezone-aware. Persist instants in UTC while retaining the
  relevant Jakarta date and reporting interval.

## Architecture

- Python 3.13, or a currently supported stable Python version if deployment
  constraints require it.
- `httpx` for HTTP, Pydantic and `pydantic-settings` for typed models and
  configuration, APScheduler for schedules, standard `sqlite3` for storage,
  pytest for tests, and Ruff for linting and formatting.
- Separate collectors, renderers, deliverers, scheduling, orchestration, and
  persistence modules.
- Collectors return structured models, not Telegram-formatted strings.
- A collector failure is isolated: all other available sections are rendered,
  and the failed section is marked unavailable.
- Monetary calculations use exact representations. Expense values are integer
  rupiah; AI provider values retain their native currency and decimal
  precision.

## Implementation steps

### 1. Scaffold and configuration

Create `pyproject.toml`, a reproducible dependency lock, `Dockerfile`,
`compose.yaml`, `config.example.yaml`, `.env.example`, `README.md`, package
modules, and tests.

Configuration must:

- reject unknown keys;
- validate credentials only for enabled integrations;
- default delivery, expenses, Vellum, ChatGPT/OpenCode, and ESC/POS to disabled;
- support credential file paths rather than requiring secret values in Docker
  environment variables;
- allow future Rclone logs to be registered through configuration;
- reject duplicate backup names, absolute configured filenames, and path
  traversal; and
- use bounded HTTP timeouts and safe retry policies.

### 2. Shared models and SQLite

Define structured digest context, collector result, backup result, provider
snapshot, money, digest report, and delivery receipt models. Results distinguish
`ok`, `failed`, `unavailable`, `stale`, and `disabled`, and carry source,
observation time, report interval, estimation status, and sanitized diagnostics.

Use transactional migrations and create storage for:

- run history and per-collector results;
- provider and cumulative-meter snapshots;
- immutable collected/rendered digest revisions;
- delivery records unique by delivery date, timezone, channel, and destination;
- delivery attempts and reconciliation history.

Enable SQLite foreign keys, busy timeout, and WAL on local cache storage. Store
decimal money as strings and expense IDR as integers. Canonical midnight slots
must be unique; manual snapshots retain their real observation time and must not
be backdated into a missed boundary.

### 3. Rclone parser and backup collector

Port the behavior of `samples/rclone_daily_report.sh` while adding conservative
failure detection:

- column-zero `YYYY/MM/DD` lines open date blocks;
- following undated summaries remain in the current block;
- leading undated lines are ignored;
- another recognized date closes the block;
- repeated blocks for the target date are included;
- `There was nothing to transfer` can be successful;
- a completed byte-oriented `Transferred:` summary can be successful;
- positive error summaries, `ERROR` lines, fatal errors, and incomplete runs
  override apparent success;
- missing, unreadable, no-entry, incomplete, failed, no-transfer, transferred,
  and unknown-size outcomes remain distinct.

The parser is streaming and pure; filesystem error handling belongs to the
collector. Diagnostics retain bounded, sanitized evidence rather than raw log
excerpts. Add synthetic and sanitized fixture tests for date inheritance,
multiple blocks, midnight boundaries, CRLF, missing files, transfer summaries,
error precedence, incomplete runs, and large inputs.

Configured files initially are:

1. `rclone-appdata-backup.log`
2. `rclone-cikopopup-backup.log`
3. `rclone-photos-backup.log`
4. `rclone-cikonet-backup.log`
5. `rclone-paperlessngx-backup.log`
6. `rclone-stash-backup.log`

Render `Backups: 6/6 OK` when all succeed. List failures only unless transfer
amounts fit concisely.

### 4. Home Assistant inspection and collector

Before finalizing parsing, issue read-only state requests using the mounted token
without exposing it. Inspect and capture allowlisted, sanitized fixtures for:

- `sensor.daily_home_consumed_energy`;
- `sensor.home_consumed_energy`;
- `sensor.internet_conectivity_uptime`.

Prefer a valid utility-meter `attributes.last_period` for previous-day energy
after confirming its cycle, reset offset, unit, and relation to the requested
date. The fallback is the difference between consecutive valid midnight
snapshots of the cumulative entity. Reject resets, unit changes, stale values,
and missing boundaries instead of fabricating usage. Energy and internet fail
independently. Historical reports may use stored observations only; a current
state must not be presented as an old historical measurement.

### 5. Orchestration, rendering, and dry-run

Run collectors independently with bounded concurrency. Sanitize exceptions and
never log authorization headers, credential-bearing URLs, or full secret-aware
configuration.

Telegram rendering uses HTML with all dynamic text escaped. Only renderer-owned
markup is emitted. Keep the result within one Telegram message and use concise,
deterministic formatting. IDR uses whole rupiah and Indonesian separators, for
example `805630` becomes `Rp805.630`.

Implement:

```text
python -m app.main run
python -m app.main collect --date YYYY-MM-DD
python -m app.main render --date YYYY-MM-DD
python -m app.main send --date YYYY-MM-DD
python -m app.main snapshot
python -m app.main healthcheck
```

`send` supports an explicit dry-run option. Dry-run never contacts Telegram or
claims a delivery record. `render` uses stored data without collection side
effects. A fixture-backed preview works without credentials.

### 6. OpenRouter and DeepSeek snapshots

OpenRouter uses `GET https://openrouter.ai/api/v1/credits` with a management
key. Store `total_credits` and cumulative `total_usage`; balance is credits minus
usage. Previous-day usage is the difference between consecutive valid Jakarta
midnight cumulative-usage snapshots. Counter decreases, account changes, and a
missing first-day snapshot are explicit unavailable/ambiguous states.

DeepSeek uses `GET https://api.deepseek.com/user/balance`. Store the complete
response in private SQLite state. Compare matching currencies between consecutive
midnight balance snapshots. A balance decrease is estimated usage because
top-ups and adjustments can affect it; balance increases are reported as an
adjustment ambiguity rather than negative usage. Never combine currencies.

### 7. Disabled expense HTTP collector

Use the internal endpoint:

```text
GET http://expenses-dashboard:8000/api/digest/daily?date=YYYY-MM-DD&timezone=Asia%2FJakarta
```

Expected schema:

```json
{
  "date": "2026-09-05",
  "timezone": "Asia/Jakarta",
  "currency": "IDR",
  "total": 805630,
  "transaction_count": 9,
  "top_category": {"name": "Transport", "total": 291230},
  "generated_at": "2026-09-09T08:40:25.397119+07:00"
}
```

Require matching date and timezone, `IDR`, whole-number nonnegative totals,
nonnegative transaction count, a valid top-category object, and an aware
`generated_at`. A later generation timestamp is valid for a historical request.
Disabled mode performs no HTTP request. Verify that internal proxynet access
bypasses NPM Basic authentication before enabling it; retain optional Basic Auth
configuration for environments that require the public route.

Read-only inspection confirmed that direct internal access requires the
non-secret `Host: expenses.cikohome.stream` header: the container-DNS Host value
returns HTTP 400, while the accepted Host value returns the expected response
without NPM Basic Auth. Keep the connection URL on proxynet and make this Host
header configurable.

### 8. Vellum platform-credit export

The managed environment exports verified organization platform-credit balance
through WebDAV every Jakarta midnight. Immutable files are named
`YYYY-MM-DD.json`; `latest.json` is safely replaced but is informational only.
The verified version-1 schema contains aware capture/as-of timestamps,
`snapshot_date`, `Asia/Jakarta`, source `vellum_platform_credits`, and USD
remaining/settled/pending credit values.

Read only the immutable files from the narrow local mount. Validate schema,
filename/date agreement, timestamps, midnight tolerance, source, currency,
freshness, stale flag, and size. Infer previous-day usage from the decrease
between consecutive midnight remaining balances and label it estimated because
top-ups and adjustments can affect it. A balance increase is ambiguous rather
than negative usage. This is one organization balance; never sum it across
Vellum instances. Per-instance usage is unavailable and must not be invented.

### 9. ChatGPT/OpenCode quota investigation

Keep this section disabled. The usage page currently viewed by the operator has
not been identified as ChatGPT web quota or Codex quota. Do not scrape browser
sessions, reuse cookies, infer percentages, or substitute documented Codex data
without confirmation. If later confirmed as Codex-backed, assess the official
`account/rateLimits/read` app-server contract on an approved authenticated host
and derive labels and remaining percentages from its response.

### 10. Scheduling and delivery safety

Create stable APScheduler jobs for midnight snapshots and 07:00 delivery with
Jakarta-aware triggers, bounded misfire handling, coalescing, and one concurrent
execution per job.

Scheduled, catch-up, and manual sends use one delivery ledger. Persist the exact
report revision and rendered payload before sending. Use explicit states such as
`claimed`, `sending`, `sent`, `failed`, and `unknown`. A timeout or crash after
possible Telegram submission becomes `unknown`; it is held for audited manual
reconciliation and is never automatically retried. Safely reclaim only
abandoned claims known to predate submission.

Validate that the Telegram destination is a private chat, retain successful
message IDs, and honor rate-limit responses. The ESC/POS deliverer remains a
disabled interface stub documenting future partial-cut support; add no printer
dependency or device access.

### 11. Container and Unraid deployment

Build a reproducible Python slim image and run non-root as `99:100`, with a
read-only root filesystem, temporary `/tmp`, dropped capabilities, and
`no-new-privileges`. Join external `proxynet`, publish no application ports, and
mount no Docker socket.

Expected mounts:

- `/mnt/cache/appdata/daily-digest/state` to `/data/state`, read-write;
- `/mnt/user/mediaserver/downloads` to `/data/rclone-logs`, read-only;
- `/mnt/user/webdav/daily-digest/vellum` to `/data/vellum-exports`, read-only;
- selected secret files to `/run/secrets/daily-digest`, read-only.

Require bind sources to exist rather than silently creating wrong directories.
Document permissions, backup/restore, unknown-send reconciliation, snapshot gaps,
and safe rollout. Do not start, replace, or alter an existing container without
approval.

## Validation

Run focused unit tests after each behavior unit, then the broader suite:

```bash
uv sync --locked --extra dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
docker compose config --quiet
docker compose build daily-digest
```

Where Docker is unavailable in the development environment, mark Docker checks
unverified and provide exact commands for Unraid. CLI and container smoke tests
must use fixture configuration and disposable state with delivery disabled.

## Risks and rollback

- Telegram has no client idempotency key; holding uncertain sends minimizes
  duplicates but may require manual intervention.
- Missed midnight snapshots cannot be reconstructed safely from current
  cumulative values.
- Rclone statistics may be ambiguous without verified terminal evidence.
- HA utility-meter semantics must be confirmed from actual state responses.
- WebDAV credentials require a dedicated folder scope and atomic publication.
- The supplied Bash sample contains an exposed Telegram credential; rotate it.
- UID 99 may be shared by other Unraid containers, so narrow mounts remain
  essential even when file modes are restrictive.

Rollback disables delivery first, returns to the prior image or code revision,
and preserves the newest delivery ledger. Back up SQLite using its backup API or
after clean shutdown, including WAL state. Never restore older delivery state
without reconciling sent dates. Do not delete persistent data, rotate credentials,
or change host ownership recursively without separate approval.

## Acceptance criteria

- [ ] All six required CLI commands work with documented semantics.
- [ ] Scheduling and catch-up use Jakarta dates and times correctly.
- [ ] Date-based delivery idempotency survives concurrent/manual/scheduled runs.
- [ ] Unknown Telegram outcomes are held for operator review.
- [ ] Collector failures are isolated and visibly marked unavailable.
- [ ] Snapshots and run history are stored in SQLite.
- [ ] Snapshot gaps and estimated provider usage are explicit.
- [ ] Rclone date-block behavior is preserved and failure evidence overrides
      apparent success.
- [ ] Actual HA entity JSON is inspected and sanitized before finalizing parsing.
- [ ] Internet is labeled `last 24h`.
- [ ] Expense collection is HTTP-only, validates integer IDR data, and is disabled
      by default.
- [ ] Vellum command findings precede schema implementation, and shared balances
      are not double-counted.
- [ ] ChatGPT/OpenCode quota remains absent until a supported source is verified.
- [ ] Telegram dynamic content is correctly HTML-escaped.
- [ ] Tests, Ruff checks, and practical container checks pass.
- [ ] Secrets do not appear in logs, fixtures, commits, or image contents.
- [ ] No Docker socket or expense SQLite database is accessed.
- [ ] A dry-run digest is shown and reviewed before any real Telegram send.
- [ ] Deployment or existing-container changes occur only after explicit approval.
