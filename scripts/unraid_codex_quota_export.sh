#!/bin/bash
set -euo pipefail

# Override these in Unraid User Scripts if your container or workspace differs.
CODEX_CONTAINER="${CODEX_CONTAINER:-openvscode-server}"
EXPORTER_PATH="${CODEX_EXPORTER_PATH:-/config/workspace/daily-digest/scripts/codex_quota_export.py}"
CODEX_BINARY="${CODEX_BINARY:-/config/bin/codex}"
OUTPUT_DIRECTORY="${CODEX_OUTPUT_DIRECTORY:-/mnt/user/webdav/daily-digest/codex}"
OUTPUT_UID="${CODEX_OUTPUT_UID:-99}"
OUTPUT_GID="${CODEX_OUTPUT_GID:-100}"
OUTPUT_FILE="${OUTPUT_DIRECTORY}/latest.json"

if [[ ! "$OUTPUT_UID" =~ ^[0-9]+$ || ! "$OUTPUT_GID" =~ ^[0-9]+$ ]]; then
    echo "CODEX_OUTPUT_UID and CODEX_OUTPUT_GID must be numeric" >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIRECTORY"
chmod 0755 "$OUTPUT_DIRECTORY"
exec 9>"${OUTPUT_DIRECTORY}/.export.lock"
flock -n 9 || exit 0

temporary="$(mktemp "${OUTPUT_DIRECTORY}/.latest.json.tmp.XXXXXX")"
trap 'rm -f "$temporary"' EXIT

# The exporter emits sanitized JSON only. Codex OAuth files remain inside the
# OpenVSCode container and are never mounted into daily-digest.
docker exec --env "CODEX_BINARY=$CODEX_BINARY" "$CODEX_CONTAINER" \
    python3 "$EXPORTER_PATH" >"$temporary"
test -s "$temporary"
chmod 0644 "$temporary"
chown -- "$OUTPUT_UID:$OUTPUT_GID" "$temporary"
mv -f "$temporary" "$OUTPUT_FILE"
trap - EXIT
