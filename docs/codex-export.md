# Codex Weekly Quota Export

Daily-digest reads a sanitized file; it never reads Codex OAuth credentials and
never starts a model turn. The exporter uses Codex app-server's
`account/rateLimits/read` method and retains only the weekly remaining percentage
and reset timestamp.

## Unraid setup

1. Ensure this repository is available inside the authenticated OpenVSCode
   container at the path configured by `CODEX_EXPORTER_PATH`.
2. Create `/mnt/user/webdav/daily-digest/codex` on the Unraid host.
3. Add `scripts/unraid_codex_quota_export.sh` to the Unraid User Scripts plugin.
   Set `CODEX_CONTAINER` if the container is not named `openvscode-server`.
4. Schedule it every 15 minutes so the default 30-minute freshness limit is met.
5. Set `codex.enabled: true` in the daily-digest configuration only after a
   successful export exists.

The wrapper defaults to:

```text
CODEX_CONTAINER=openvscode-server
CODEX_EXPORTER_PATH=/config/workspace/daily-digest/scripts/codex_quota_export.py
CODEX_BINARY=/config/bin/codex
CODEX_OUTPUT_DIRECTORY=/mnt/user/webdav/daily-digest/codex
CODEX_OUTPUT_UID=99
CODEX_OUTPUT_GID=100
```

All values can be overridden as environment variables in User Scripts. The
wrapper uses `flock`, writes a temporary file, assigns it to UID/GID `99:100`
with mode `0644`, and replaces `latest.json` only after the exporter succeeds.
Failed runs leave the prior export untouched. `CODEX_BINARY` is passed into the
non-interactive container process because its PATH may differ from an interactive
OpenVSCode terminal.

## Sanitized schema

```json
{
  "schema_version": 1,
  "source": "codex_app_server_rate_limits",
  "captured_at": "2026-09-09T10:00:00Z",
  "bucket_id": "codex",
  "weekly": {
    "used_percent": "50",
    "remaining_percent": "50",
    "window_minutes": 10080,
    "resets_at": "2026-09-15T10:20:32Z"
  }
}
```

Daily-digest requires this exact schema, rejects files over the configured size,
and marks exports older than `max_age_minutes` as stale. Do not add account data,
tokens, cookies, authorization headers, or unsanitized app-server responses.
