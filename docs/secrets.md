# Secret Provisioning and Handling

## Policy

Secret values must never be:

- pasted into chat or issue text;
- printed to a terminal or application log;
- placed in command-line arguments or URLs;
- committed to Git, fixtures, configuration examples, or container images;
- included in exception messages or HTTP debug traces; or
- stored in the general application configuration file.

During development, secret files are already mounted at:

```text
/run/secrets/daily-digest
```

Only the required file path or configured variable name should be communicated.
Do not enumerate or read unrelated files in that directory.

## Recommended host layout

Keep secret material outside the source repository and separate from mutable
SQLite state. A suitable Unraid layout is:

```text
/mnt/cache/appdata/daily-digest/
├── state/
└── secrets/
```

Use one file per secret. Expected categories include Home Assistant, Telegram,
OpenRouter, DeepSeek, and optional expense Basic Auth credentials. Exact local
filenames are operator-controlled and are referenced through `*_FILE`
configuration values.

The service runs as UID/GID `99:100`. A typical starting point is mode `0700`
for the dedicated secret directory and `0600` for individual secret files,
owned by the runtime identity. Confirm compatibility with Unraid and backup
tools before changing permissions. Never recursively change ownership or modes
on an existing application or WebDAV share.

UID 99 is commonly shared on Unraid. File ownership alone is therefore not a
complete isolation boundary. Mount only the exact secret directory or files into
the daily-digest container, read-only, and do not mount them into unrelated
containers.

## Application configuration

Prefer environment variables containing paths, not values. Illustrative names:

```text
DAILY_DIGEST_HOME_ASSISTANT_TOKEN_FILE=/run/secrets/daily-digest/home_assistant_token
DAILY_DIGEST_TELEGRAM_BOT_TOKEN_FILE=/run/secrets/daily-digest/telegram_bot_token
DAILY_DIGEST_TELEGRAM_CHAT_ID_FILE=/run/secrets/daily-digest/telegram_chat_id
DAILY_DIGEST_OPENROUTER_MANAGEMENT_KEY_FILE=/run/secrets/daily-digest/openrouter_management_key
DAILY_DIGEST_DEEPSEEK_API_KEY_FILE=/run/secrets/daily-digest/deepseek_api_key
```

If public expense access is ever needed, username and password should also use
separate file-path settings. Internal `expenses-dashboard:8000` access is
expected not to require those credentials and must be verified.

Docker Compose file-backed secrets are a mounting mechanism, not encrypted
storage. Host permissions and narrowly scoped mounts remain necessary. Direct
secret-valued environment variables and `.env` files are less desirable because
values can appear in container inspection and administrative tooling.

## Safe development inspection

Live integration inspection must:

1. Use only approved read-only endpoints.
2. Read only the explicitly configured secret file.
3. Keep the credential in process memory and attach it through an HTTP header.
4. Disable verbose HTTP logging and never serialize request headers.
5. Print only status, schema information, and allowlisted response fields.
6. Sanitize fixtures by construction rather than attempting to redact an
   unrestricted dump afterward.
7. Avoid shell tracing and commands that embed a secret via substitution.
8. Never contact Telegram during inspection.

Read-only mounting prevents modification but does not prevent a process from
using a credential. Prefer dedicated, least-privilege credentials where the
provider supports them, and revoke temporary development credentials after use.

## Logging and error handling

- Redact `Authorization`, cookies, API keys, passwords, and bot tokens.
- Never log complete settings objects.
- Telegram bot tokens occur in the request URL; log only a fixed service name
  and method, not the URL.
- Persist sanitized error codes and brief operator-safe messages rather than raw
  HTTP request/response dumps.
- Tests must use unmistakably fake values and assert they do not appear in logs,
  tracebacks, persisted collector results, or rendered messages.

## Rotation and incidents

The supplied sample Bash script contains a hard-coded Telegram credential. Its
value must not be copied into the new application and should be rotated before
deployment. Sanitizing or deleting the sample is a separate destructive or
scope-changing action and requires approval.

If exposure is suspected:

1. Disable affected delivery or collector configuration.
2. Revoke or rotate the provider credential.
3. Replace its host secret file without committing it.
4. Restart only the affected service through an approved deployment action.
5. Review sanitized logs and Git history without reproducing the credential.
