#!/usr/bin/env bash
# Fetch bot-side secrets from GSM at boot and write to
# /etc/bot/secrets.env (mode 0640, root:bot). bot.service loads
# this via EnvironmentFile=.
#
# Same pattern as server/lavalink/scripts/fetch-secrets.sh and
# server/scripts/fetch-secrets.sh -- read via the GCE metadata
# server's access-token endpoint, no static credentials on disk.
#
# Secrets pulled here:
#   - LAVALINK_PASSWORD: bot -> Lavalink WS auth. Even though Lavalink
#     is on localhost, it still rejects unauthenticated WS handshakes,
#     so the bot needs the password as an env var. NOT pulled at
#     runtime via LAVALINK_PASSWORD_SECRET_PATH (the way we do for
#     the Valheim password) because adding a GSM call per /music play
#     is noise -- one fetch at boot is plenty.
#
# Defaults to the standard secret IDs; override via systemd
# Environment= if you ever want a different one (rotation testing).

set -euo pipefail

SECRET_NAME="${LAVALINK_PASSWORD_SECRET_NAME:-lavalink-server-password}"
SECRET_FILE="/etc/bot/secrets.env"
META="http://metadata.google.internal/computeMetadata/v1"
HDR="Metadata-Flavor: Google"

PROJECT_ID="$(curl -fsS -H "$HDR" "$META/project/project-id")"
ACCESS_TOKEN="$(curl -fsS -H "$HDR" "$META/instance/service-accounts/default/token" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"

PAYLOAD_B64="$(curl -fsS -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${SECRET_NAME}/versions/latest:access" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["payload"]["data"])')"

LAVALINK_PASSWORD="$(printf '%s' "$PAYLOAD_B64" | base64 -d)"

# Same length check the Lavalink fetch-secrets does. Catches the
# "secret was seeded wrong" failure at boot rather than at
# /music play time when it'd produce confusing auth errors.
if [[ ${#LAVALINK_PASSWORD} -lt 16 ]]; then
  echo "ERROR: Lavalink password from GSM is shorter than 16 chars" >&2
  exit 1
fi

install -d -m 0750 -o root -g bot /etc/bot
install -m 0640 -o root -g bot /dev/null "$SECRET_FILE"
printf 'LAVALINK_PASSWORD=%s\n' "$LAVALINK_PASSWORD" > "$SECRET_FILE"
