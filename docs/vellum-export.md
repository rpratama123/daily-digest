# Vellum Platform-Credit Export

## Verified transport

Vellum Assistant runs a no-LLM script every midnight in `Asia/Jakarta` and
publishes platform-credit snapshots through the existing WebDAV service:

```text
daily-digest/vellum/YYYY-MM-DD.json
daily-digest/vellum/latest.json
```

The host directory is `/mnt/user/webdav/daily-digest/vellum`, mounted read-only
in the digest container at `/data/vellum-exports`.

Dated files are immutable. `latest.json` is replaced safely and is informational
only; daily-digest never uses it to calculate historical usage. Retries verify an
existing daily record instead of replacing history.

## Verified schema

The sanitized sample has this contract:

```json
{
  "captured_at": "2026-09-09T09:03:58Z",
  "credits": {
    "as_of": "2026-09-09T09:04:01.962Z",
    "pending": 0,
    "remaining": 6.23,
    "settled": 6.23,
    "stale": false,
    "unit": "USD"
  },
  "schema_version": 1,
  "snapshot_date": "2026-09-09",
  "source": "vellum_platform_credits",
  "timezone": "Asia/Jakarta"
}
```

The importer strictly validates the schema version, source, timezone, USD unit,
date and filename agreement, aware timestamps, nonnegative finite amounts,
payload size, freshness, and the upstream stale flag. Numeric JSON values are
decoded directly as `Decimal` to avoid binary floating-point calculations.

## Usage calculation

Vellum exposes current remaining organization credit, not direct daily usage.
Daily usage is therefore estimated from consecutive midnight balances:

```text
estimated usage = prior midnight remaining - current midnight remaining
```

The estimate is labeled with `~` because top-ups and adjustments can alter the
balance independently of usage. A balance increase is reported as an adjustment
ambiguity rather than negative usage.

Both immutable files must be present:

- the report date, representing its opening midnight balance; and
- the digest delivery date, representing its closing midnight balance.

Captures must fall within the configured midnight tolerance. The initial
`2026-09-09.json` sample was captured around 16:04 WIB, so it is useful for schema
verification and current balance confirmation but is deliberately not accepted
as a canonical midnight boundary. With scheduled exports beginning at midnight
on 2026-09-10, the first complete daily estimate becomes available after the
2026-09-11 midnight snapshot.

## Multiple instances

This export is an organization-level platform-credit balance. Daily-digest reads
one dated organization snapshot and does not sum balances from multiple Vellum
instances. Per-instance usage is not available in this schema and is not
invented or inferred.

## Failure behavior

Missing, stale, malformed, non-midnight, or conflicting snapshots make only the
Vellum section unavailable. Other digest sections continue rendering. Temporary
uploads and `latest.json` cannot substitute for an immutable dated boundary.

## Security

- The cloud exporter uses a dedicated, folder-restricted WebDAV account over
  verified HTTPS.
- WebDAV write credentials remain in the managed cloud environment.
- The digest receives only a narrow read-only mount.
- Exports contain balance metadata but no API credentials.
- No remote command execution, LLM invocation, or Docker socket is involved.
