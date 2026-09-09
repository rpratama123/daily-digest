# Integration Findings

Last updated: 2026-09-09

This document records confirmed contracts separately from assumptions. It must
not contain credentials, authorization headers, cookies, or unsanitized service
responses.

## Home Assistant

### Confirmed

- Reachable candidates are `http://10.20.30.55:8123` and
  `https://ha.cikohome.stream`.
- Home Assistant uses host networking, so the digest must not assume
  `localhost` or proxynet container DNS.
- Entities:
  - `sensor.daily_home_consumed_energy`
  - `sensor.home_consumed_energy`
  - `sensor.internet_conectivity_uptime`
- The internet sensor is rolling 24-hour availability and is labeled `last 24h`.
- A long-lived token has been provisioned as a mounted secret.

### Read-only inspection findings

The three entity-state endpoints were inspected on 2026-09-09 using the mounted
token. Only allowlisted fields were displayed and captured in sanitized fixtures.

- Daily energy uses `kWh`, `device_class: energy`, and
  `state_class: total_increasing`. It exposes string-valued `last_period` and an
  aware `last_reset`. The observed reset instant was approximately 00:00 Jakarta,
  confirming that `last_period` represents the preceding Jakarta day at that
  boundary.
- Cumulative energy uses `kWh`, `device_class: energy`, and `state_class: total`.
  Its state is a decimal string and its source is a power-meter integration
  sensor.
- Internet availability uses `%` and `state_class: measurement`; its state is a
  decimal percentage. Home Assistant may leave `last_updated` unchanged while a
  stable value remains unchanged, so that timestamp alone is not used to reject
  a live read as stale.

The collector prefers `attributes.last_period` only when the aware reset date in
Jakarta equals the digest delivery date. Otherwise it requires consecutive
midnight cumulative snapshots. Reset counters, invalid units, and missing
boundaries are unavailable rather than inferred.

## Rclone backups

The source behavior is in `samples/rclone_daily_report.sh`.

Confirmed selection behavior:

- A line beginning at column zero with `YYYY/MM/DD` changes the active date.
- Undated lines belong to the last recognized date block.
- Leading undated lines are ignored.
- All matching date blocks are included, even when separated by other dates.

Required improvements are conservative failure precedence for positive error
counts, `ERROR` lines, fatal errors, and incomplete runs. Real sanitized logs are
still needed to verify exact completion and error syntax. The six initial log
names are listed in the approved plan and remain configurable.

The sample script contains a hard-coded Telegram credential. It was not printed
or used and should be rotated before deployment.

## Expenses

### Confirmed production contract

- Internal URL: `http://expenses-dashboard:8000` over external Docker network
  `proxynet`.
- Endpoint: `GET /api/digest/daily`.
- Parameters: `date=YYYY-MM-DD` and `timezone=Asia/Jakarta`.
- Public URL is protected by NPM HTTP Basic Auth.
- Internal container access bypasses NPM authentication. A read-only request on
  2026-09-09 confirmed that the upstream rejects its container-DNS host name
  with HTTP 400 but accepts `Host: expenses.cikohome.stream`, returning HTTP 200.
  The collector therefore connects to the internal proxynet URL while sending
  that configurable, non-secret Host header.
- The collector must not inspect or query SQLite.

Confirmed response shape:

```json
{
  "date": "2026-09-05",
  "timezone": "Asia/Jakarta",
  "currency": "IDR",
  "total": 805630,
  "transaction_count": 9,
  "top_category": {
    "name": "Transport",
    "total": 291230
  },
  "generated_at": "2026-09-09T08:40:25.397119+07:00"
}
```

Expense money is always whole-number IDR. `total` and `top_category.total` are
validated as nonnegative integers with no rounding. `generated_at` is an aware
timestamp and may be later than the requested historical date. The response
date must equal the requested report date.

## OpenRouter

Confirmed from the published API contract:

- `GET https://openrouter.ai/api/v1/credits`.
- Bearer authentication uses a management key.
- Response contains `data.total_credits` and `data.total_usage`.
- Balance is `total_credits - total_usage`.
- Previous-day usage requires differences between consecutive midnight
  cumulative-usage snapshots.

Native currency precision is preserved. Missing initial or boundary snapshots
must be reported rather than inferred.

## DeepSeek

Confirmed from the published API contract:

- `GET https://api.deepseek.com/user/balance`.
- Response contains `is_available` and a `balance_infos` list.
- Each balance has a currency and string values for total, granted, and topped-up
  balances.
- The complete response is retained in private SQLite state.

Estimated previous-day usage is inferred from matching-currency balance changes
between midnight snapshots. It must be labeled estimated because top-ups and
adjustments can change the balance independently of usage.

## Vellum AI Assistant

The managed Vellum environment can export organization platform-credit balance,
but not direct previous-day usage. A no-LLM script publishes immutable
`YYYY-MM-DD.json` files and an atomically replaced `latest.json` through the
dedicated WebDAV directory. The verified schema is version 1 with source
`vellum_platform_credits`, timezone `Asia/Jakarta`, USD remaining/settled/pending
credits, and aware capture/as-of timestamps.

Daily-digest uses only consecutive immutable midnight files. Usage is the
estimated decrease in remaining balance and is marked `~`; top-ups and
adjustments can invalidate that inference. The initial 2026-09-09 sample was a
manual afternoon capture and is not accepted as midnight. Scheduled captures
begin on 2026-09-10, making 2026-09-10 usage available after the 2026-09-11
snapshot. `latest.json` is never used for historical differences. See
`docs/vellum-export.md` for the complete contract.

## Codex quota

A read-only probe against authenticated `codex-cli 0.153.4` confirmed that the
official app-server method `account/rateLimits/read` returns a `codex` bucket.
Its secondary window is 10,080 minutes (one week) and includes `usedPercent` and
Unix-seconds `resetsAt`. The digest reports `100 - usedPercent` and converts the
reset instant to the configured Jakarta date.

The exporter runs inside the existing OpenVSCode container, starts no model
turn, and writes only an allowlisted schema through stdout. An Unraid host script
atomically publishes that output for a read-only daily-digest mount. Codex OAuth
files remain inside OpenVSCode. Direct calls to the undocumented WHAM endpoint,
browser session scraping, credential sharing, and a Docker socket in the digest
container remain out of scope. The integration is disabled by default.

## Telegram

- Destination is a private chat.
- Rendering uses Telegram HTML with escaping of every dynamic value.
- No real send is permitted before dry-run review and explicit approval.
- Telegram does not provide a client idempotency key. A request that may have
  succeeded before a timeout is recorded as unknown and held for manual
  reconciliation rather than automatically retried.

## Unraid and container constraints

- Runtime UID/GID: `99:100`.
- No Docker socket.
- No published application port is required.
- State is writable only under the dedicated persistent data mount.
- Backup logs, Vellum exports, Codex exports, and secrets are mounted read-only.
- UID 99 can be shared by other Unraid services; narrow per-container mounts are
  required in addition to host file permissions.
